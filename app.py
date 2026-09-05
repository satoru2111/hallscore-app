"""🦉 ホールスコア — スロット店舗のデータ採点 (旧称: フクロウ)

データ: data/fukurou_v2.db (build_db.py → engine_v0.py で構築)
画面: セグメント切替 ①狙い目 ②ジャグラー ③店詳細。カードの店名タップで店詳細へ遷移
      (st.tabs はプログラム切替不可のため session_state ベースのナビにしている)

起動: .venv/bin/streamlit run app_v2.py
"""
import datetime as dt
import html as _h
import json
import math
import re
import sqlite3
import urllib.parse
import urllib.request
import os
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
# 公開配信 (Streamlit Cloud) 用。R2 から serve.db を取得する。
# このブロックだけが本体 app_v2.py との差分 (scripts/sync_public_app.py が生成)
from db_source import ensure_db  # noqa: E402
try:
    DB = ensure_db()
except Exception as _e:      # 黙って空DBを掴ませない。原因をそのまま出して止める
    st.error("データの読み込みに失敗しました")
    st.code(str(_e))
    st.stop()


@st.cache_data(ttl=600)


@st.cache_data(ttl=1800)
def nodata_halls() -> pd.DataFrame:
    """みんパチにあって差枚データが無い店。掲載店には数えない (2026-09-03)."""
    try:
        return q("""SELECT hall, pref, city, address, n_slot, slot_exchange_mai,
                           open_time, entry_method, entry_time
                    FROM halls_nodata ORDER BY hall""")
    except Exception:
        return pd.DataFrame()


def show_nodata_hall(name: str) -> None:
    """差枚データが無い店の説明。持っている事実だけを出す."""
    d = nodata_halls()
    r = d[d["hall"] == name]
    if r.empty:
        st.session_state.pop("nodata_pick", None)
        return
    r = r.iloc[0]
    if st.button("← 店舗を探すに戻る", key="nod_back"):
        st.session_state.pop("nodata_pick", None)
        st.rerun()
    st.markdown(f"### {name}")
    loc = " ".join(x for x in (r["pref"], r["city"]) if isinstance(x, str) and x)
    if loc:
        st.caption(loc + (f" ・ スロット{int(r['n_slot'])}台"
                          if pd.notna(r["n_slot"]) else ""))
    st.warning("この店は台ごとの差枚が公開されていないため、"
               "出玉の分析ができません。以下は店舗情報のみです。")
    ent = entry_line(r)
    rate = r["slot_exchange_mai"]
    line = [x for x in (ent, f"交換率 {rate}枚" if pd.notna(rate) else None) if x]
    if line:
        st.markdown(f"🚪 {' ｜ '.join(line)}")
    if isinstance(r["address"], str) and r["address"]:
        st.caption(r["address"])
    st.caption("差枚が公開されれば自動的に分析対象になります。")



@st.cache_data(ttl=1800)
def search_options() -> list[str]:
    """店名検索の候補。市区も混ぜて「香取」でも引けるようにする (2026-09-03).

    5,388件・262KB あるが、Streamlit は 10KB 以上の描画メッセージを
    サーバ側でキャッシュし、2回目以降はハッシュ参照だけを送る
    (global.minCachedMessageSize)。毎回の再送にはならない。
    """
    c = halls.set_index("hall")["city"].to_dict()
    out = [f"{h}（{c[h]}）" if isinstance(c.get(h), str) and c[h] else h
           for h in halls["hall"]]
    nod = nodata_halls()
    out += [(f"{r['hall']}（{r['city']}・データなし）"
             if isinstance(r["city"], str) and r["city"] else f"{r['hall']}（データなし）")
            for _, r in nod.iterrows()]
    return sorted(out)


def hall_search(key: str = "gsearch") -> None:
    """店名で探し、その場に結果カードを出す。

    最も多い使い方は「自分が通っている店を調べる」で、求めているのは
    あるかないかの即答。画面遷移を挟むと遅い。選んだらその場に出し、
    詳しく見たい人だけ遷移する (2026-09-03 ユーザー方針)。
    """
    pick = st.selectbox("店名で探す", search_options(), index=None,
                        placeholder="店名や市区を入力 (例: マルハン、豊島区)",
                        key=key, label_visibility="collapsed")
    if pick:
        hall_result_card(pick.split("（")[0], "データなし" in pick)


def _go_detail(hall: str) -> None:
    """カードの「詳しく見る」→ 店舗詳細へ。

    `page` はウィジェットのキーなので on_click の中で代入する。
    """
    st.session_state["hall_pick"] = hall
    st.session_state["page"] = "店舗"


def _basic_line(r) -> str:
    """開店時刻・抽選・交換率の1行 (無い項目は落とす)."""
    parts = [x for x in (entry_line(r),
                         f"交換率 {r['slot_exchange_mai']}枚"
                         if pd.notna(r.get("slot_exchange_mai")) else None) if x]
    return " ｜ ".join(parts)


def hall_result_card(hall: str, nodata: bool) -> None:
    """検索結果を1枚のカードで出し、ここで完結させる."""
    if nodata:
        d = nodata_halls()
        r = d[d["hall"] == hall]
        if r.empty:
            return
        r = r.iloc[0]
        loc = " ".join(x for x in (r["pref"], r["city"]) if isinstance(x, str) and x)
        with st.container(border=True, key=f"res_{_ci()}"):
            st.markdown(f"**{hall}** &nbsp;<span class='c-tag c-cool'>データなし</span>"
                        f"<br><span class='c-sub'>{loc}</span>", unsafe_allow_html=True)
            st.caption("台ごとの差枚が公開されていないため、出玉の分析ができません。")
            if (ln := _basic_line(r)):
                st.markdown(f"🚪 {ln}")
        return

    h = halls[halls["hall"] == hall]
    if h.empty:
        return
    h = h.iloc[0]
    loc = " ".join(x for x in (h["pref"], h["city"]) if isinstance(x, str) and x)
    with st.container(border=True, key=f"res_{_ci()}"):
        st.markdown(f"**{hall}**<br><span class='c-sub'>{loc}</span>",
                    unsafe_allow_html=True)
        bits = []
        po = q("SELECT mai FROM hall_payout_stats WHERE hall=? AND kind='normal'", (hall,))
        if not po.empty and pd.notna(po["mai"].iloc[0]):
            bits.append(f"普段の出方 **{po['mai'].iloc[0]:+,.0f}枚/台**")
        jr = q("SELECT mean_setting FROM hall_juggler_stats WHERE hall=?", (hall,))
        if not jr.empty and pd.notna(jr["mean_setting"].iloc[0]):
            v = jr["mean_setting"].iloc[0]
            bits.append(f"ジャグラー **{v:.2f}**（{juggler_label(v)}）")
        st.markdown(" ・ ".join(bits) if bits else "この店の集計はまだ準備中です。")
        if (ln := _basic_line(h)):
            st.caption(f"🚪 {ln}")
        st.button("詳しく見る", key=f"godetail_{_ci()}", on_click=_go_detail, args=(hall,))


def nodata_section() -> None:
    """エリアを絞ったとき、その地域のデータがない店を一覧の末尾に出す.

    「近所の店が載っていない」を防ぐ。全国では 2,800店 あって埋もれるので、
    絞り込んだときだけ、畳んだ状態で件数を見せる (2026-09-03 ユーザー方針)。
    """
    stt = scope_state("geo")
    if stt.get("mode") != "都道府県":
        return
    pref, reg = stt.get("pref"), stt.get("region")
    if not reg:
        reg = PREF_REGION.get(pref, "すべて")
    if reg == "すべて":
        return
    d = nodata_halls()
    if d.empty:
        return
    sub = (d[d["pref"].isin(REGIONS.get(reg, []))] if pref in (None, "すべて")
           else d[d["pref"] == pref])
    city = stt.get("city")
    if city and city != "すべて":
        sub = sub[city_mask(sub["city"], city)]
    if sub.empty:
        return
    with st.expander(f"この地域のデータがない店 {len(sub)}件"):
        st.caption("台ごとの差枚が公開されていないため、出玉の分析ができません。"
                   "店舗情報のみ掲載しています。")
        for _, r in sub.head(60).iterrows():
            ln = _basic_line(r)
            city_s = str(r["city"]) if isinstance(r["city"], str) else ""
            st.markdown(
                f'<div class="ndrow"><b>{_h.escape(str(r["hall"]))}</b>'
                f'<span>{_h.escape(city_s)}{" ・ " + _h.escape(ln) if ln else ""}</span></div>',
                unsafe_allow_html=True)
        if len(sub) > 60:
            st.caption(f"他 {len(sub) - 60}件（市区で絞ると全部出ます）")



@st.cache_data(ttl=1800)
def jug_rank() -> tuple[dict, int]:
    """予想設定の順位。全店が 3.08〜3.66 に収まり SD は 0.080 しかないので、
    数字だけでは高いか低いか分からない (2026-09-03 ユーザー指摘)。

    母集団は画面に出しているのと同じ n_unit_days>=100 の店。
    """
    d = q("""SELECT hall, mean_setting FROM hall_juggler_stats
             WHERE n_unit_days >= 100 AND mean_setting IS NOT NULL""")
    if d.empty:
        return {}, 0
    d = d.sort_values("mean_setting", ascending=False).reset_index(drop=True)
    n = len(d)
    # 上位 x%。1位なら 1%、最下位なら 100%
    return {r["hall"]: max(1, round(100 * (i + 1) / n))
            for i, r in d.iterrows()}, n



# 店の位置づけを次元ごとに出す。合成得点にしないのは、次元どうしの相関が
# 低いため (ジャグラー × AT は r=+0.175)。1つの点数にすると、
# 何が良い店なのかが消える (2026-09-03)
#
# 信頼性 0.80 を満たした次元だけを載せる。普段の出方 (payout) は
# 0.636〜0.738 で基準に届かないため入れない
DIMS = [
    ("ジャグラー", "設定が高い", "SELECT hall, mean_setting v FROM hall_juggler_stats "
                              "WHERE n_unit_days >= 100 AND mean_setting IS NOT NULL", True),
    ("稼働", "よく回っている", "SELECT hall, avg_games v FROM hall_util_stats "
                            "WHERE n_unitdays >= 100 AND avg_games IS NOT NULL", True),
    ("イベント日の空き", "座りやすい", "SELECT hall, ev_idle v FROM hall_event_crowd "
                                  "WHERE n_event >= 3 AND ev_idle IS NOT NULL", True),
]


@st.cache_data(ttl=1800)
def dim_ranks() -> dict:
    """次元ごとの順位表。{次元: (店→上位x%, 母数)}"""
    out = {}
    for name, _good, sql, desc in DIMS:
        d = q(sql)
        if d.empty:
            continue
        d = d.sort_values("v", ascending=not desc).reset_index(drop=True)
        n = len(d)
        out[name] = ({r["hall"]: max(1, round(100 * (i + 1) / n))
                      for i, r in d.iterrows()}, n)
    return out


def dim_panel(hall: str) -> None:
    """この店が何で強いのかを、次元ごとの順位で見せる."""
    rk = dim_ranks()
    rows = [(name, good, rk[name][0][hall], rk[name][1])
            for name, good, _s, _d in DIMS
            if name in rk and hall in rk[name][0]]
    if not rows:
        return
    st.markdown("##### 📊 この店の位置づけ")
    bars = []
    for name, good, pct, n in rows:
        # 上位1%が右端に来るように反転する
        w = max(2, 100 - pct)
        tone = "hot" if pct <= 10 else "warm" if pct <= 25 else (
            "cool" if pct >= 75 else "")
        lab = f"上位{pct}%" if pct <= 50 else f"下位{101 - pct}%"
        bars.append(
            f'<div class="dimrow"><span class="dimname">{name}</span>'
            f'<span class="dimbar"><i class="dim-{tone}" style="width:{w}%"></i></span>'
            f'<span class="dimpct">{lab}</span></div>')
    st.markdown("".join(bars), unsafe_allow_html=True)
    st.caption("全店の中での位置。帯が長いほど"
               + "／".join(f"{n}は{g}" for n, g, _p, _c in rows)
               + "。普段の出方は店ごとの違いが再現しないため入れていません。")


def scope_context(df_before, df_after, what: str) -> None:
    """いま何を表示しているかを1行で出す.

    既定の「本日」は今日イベント日の店だけを並べるので、全国から見ると
    自分の店はまず出ない。それを「掲載されていない」と読まれていた
    (2026-09-03 利用者の指摘)。母数と絞り込みを明示する。
    """
    st_ = scope_state("geo")
    area = scope_summary("geo")
    n_all = len(halls)
    if st_.get("mode") == "すべて":
        st.caption(f"**全国{n_all:,}店**のうち、{what}**{len(df_after)}店**を表示中。"
                   "エリアを選ぶと近くの店だけになります。")
        return
    # 絞った結果が0件のとき、「イベント日が無い」のか「掲載が無い」のかを分ける
    n_area = len(scope_apply(halls[["hall", "pref", "city"]].copy(), st_))
    if len(df_after) == 0:
        if n_area:
            st.warning(f"**{area}**には{what}店がありません。"
                       f"掲載は**{n_area}店**あります。")
            st.button(f"{area}の掲載{n_area}店を見る", key="to_halls_from_empty",
                       on_click=_go_halls)
        else:
            st.warning(f"**{area}**に掲載している店がまだありません。")
        return
    st.caption(f"**{area}の掲載{n_area}店**のうち、{what}**{len(df_after)}店**を表示中。")


def _go_halls() -> None:
    """店舗一覧へ移動する (ボタンの on_click 用).

    `page` はウィジェットのキーなので、スクリプト本体から代入すると
    StreamlitAPIException になる。コールバックはウィジェット生成より前に
    走るので代入できる (2026-09-03)。
    """
    st.session_state["page"] = "店舗"
    st.session_state["hall_pick"] = None
    st.session_state["nodata_pick"] = None


def _crowd_kind() -> str:
    """いま見ている画面が、イベント日と通常日のどちらを扱っているか.

    ランキングは通常日の出方の順位なので、イベント日の混雑を出しても
    日が噛み合わない (2026-09-03)。
    """
    return "normal" if st.session_state.get("page") == "ランキング" else "event"


def crowd_toggle() -> None:
    """混み具合を一覧に出すかの切り替え。

    一覧のすぐ上に置く。ページ見出しより前に置いていたときは、何に効く操作か
    分からず見落とされた (2026-09-03 ユーザー指摘)。効果の出る画面
    (本日・イベント・ランキング) にだけ出す。
    """
    ev = _crowd_kind() == "event"
    st.checkbox("イベント日に座れるかも表示" if ev else "普段の混み具合も表示",
                key="show_crowd_pill",
                help="満台になりやすい店に赤いピルが付きます。"
                     "既定では出しません（店の評価と混同しやすいため）")



# 「高設定の入りやすさ」(high_share の順位) は表示から外した (2026-09-04)。
#   ・平均予想設定との相関が r=+0.81 / ρ=+0.80 で、情報の大半が重複していた
#     (上位50店の重なり 80%)
#   ・EM の一様事前ゆえ絶対値が上振れし、高設定率 94.7% の店まで出ていた。
#     絶対値を出せないので順位しか見せられず、その順位の意味も伝わらない
#   ・「ジャグラーが甘い」との違いを説明できない指標は使われない (ユーザー判断)
# engine 側の計算と列は残してある。再検討するならそこから。


def entry_line(h) -> str:
    """開店時刻・抽選方式・締切を1行にする。無い項目は落とす.

    みんパチのプロフィール由来。全店に揃ってはいない (開店時刻 1,168店 /
    抽選方式 1,231店 / 締切 1,127店) ので、欠けている項目は書かない
    (2026-09-02)。
    """
    def g(k):
        v = h.get(k) if hasattr(h, "get") else h[k]
        return v if isinstance(v, str) and v.strip() and v.strip() != "-" else None

    op, method, cut = g("open_time"), g("entry_method"), g("entry_time")
    parts = []
    if op:
        parts.append(f"{op} 開店")
    if method:
        parts.append(f"{method} {cut} 締切" if cut else method)
    elif cut:
        parts.append(f"入場 {cut} 締切")
    return " / ".join(parts)


def juggler_label(v: float) -> str:
    """全店の中でこの予想設定がどのあたりかを、打ち手の言葉で返す.

    数値だけでは判断できない (2026-09-02 ユーザー指摘)。分布が極端に狭く
    (平均3.37・SD 0.104、5〜95%tile が 3.22〜3.54)、「3.4」と言われても
    上位40%程度であることが伝わらない。

    「優秀」ではなく「甘い/辛い」を使う。上位10%の店でも通常日は
    ほぼトントン (+4枚/日) で、絶対的な良さを約束できないため。
    甘い/辛いは打ち手が普段この意味で使う語で、相対的な表現でもある。

    順位が信頼できることは確認済み。scripts/validate_metric.py で測ると
    台日数100以上で信頼性0.835、300以上で0.867 (2026-09-02)。
    共通基準の0.80を満たす最小が100なのでそこを下限にする (780店が対象)。

    ⚠️ 未解決: juggler_daily は event/control/daily を全部含み、県ごとの
    構成比が偏っている。同じ店の中で比べると daily は control より
    予想設定が +0.036 高く、店ごとのばらつき0.068の半分にあたる系統差がある。
    daily のみに絞ると偏りは消えるが、下限が n>=1,000 になり対象が55店まで
    落ちて指標として成立しない。全日収集で daily が増えたら再測定して
    切り替える。
    """
    if v is None or pd.isna(v):
        return ""
    allv = q("""SELECT mean_setting FROM hall_juggler_stats
                WHERE mean_setting IS NOT NULL AND n_unit_days >= 100""")
    if allv.empty:
        return ""
    above = (allv["mean_setting"] > v).mean()      # 自分より上の割合
    if above <= 0.10:
        return "激甘"
    if above <= 0.25:
        return "甘め"
    if above <= 0.75:
        return "ふつう"
    if above <= 0.90:
        return "辛め"
    return "激辛"


# 甘い側は目立たせ、辛い側も色を付ける。避けたい店ほど目に入るべきなので
# 悪い方も無色にしない (2026-09-02)
JUG_TONE = {"激甘": "hot", "甘め": "warm", "ふつう": "", "辛め": "cool", "激辛": "cold"}


@st.cache_data(ttl=600)
def util_label(hall: str) -> tuple:
    """店の稼働 (空き具合) を返す (ラベル, 1台あたり平均G数, 遊休率, 点数).

    「+501枚の激甘」と出ていても満台で座れなければ意味がない。
    空いている店は抽選に負けても打てる、という実用情報 (2026-09-02)。

    信頼性は 0.92〜0.93 で、今の指標の中で最も高い
    (ジャグラー 0.78 / AT機 0.67)。店の性質として安定している。

    2026-09-04 (Issue #3 項目5) に daily に統制して測り直したところ 0.949。
    パーセンタイルに直した誤差は ±6点で、**5つの次元の中でここだけが点数で
    出せる**。出玉系は ±14〜17点あり、甘辛ラベルは隣り合う段階が誤差の
    範囲で入れ替わりうる (ユーザー判断で5段階を維持している)。
    点数は「空いているほど高い」向き (打てる可能性の高さ)。
    """
    df = q("""SELECT hall, avg_games, idle_rate FROM hall_util_stats
              WHERE n_unitdays >= 200 AND avg_games IS NOT NULL""")
    # 戻り値を4要素にしたとき (2026-09-04) ここを3要素のまま残し、稼働データの無い店で
    # 店舗詳細が ValueError で落ちた。早期 return も同じ形にする
    if df.empty:
        return ("", None, None, None)
    row = df[df["hall"] == hall]
    if row.empty:
        return ("", None, None, None)
    g = float(row["avg_games"].iloc[0])
    idle = float(row["idle_rate"].iloc[0])
    above = (df["avg_games"] > g).mean()
    lab = ("かなり混む" if above <= .10 else "混む" if above <= .25 else
           "ふつう" if above <= .75 else "空いてる" if above <= .90 else "かなり空いてる")
    # 空いているほど高い点。誤差 ±6点なので 10点の差には意味がある
    score = round(above * 100)
    return (lab, g, idle, score)


@st.cache_data(ttl=600)
def crowd_label(hall: str) -> tuple:
    """イベント日の混み具合 (ラベル, 倍率, 遊休率).

    抽選人数は公開データに無い。X の投稿から拾う案は、投稿が「多かった日」に
    偏る (欠測が結果と相関する) うえ規約と費用の問題があるので採らない。
    回転数の比なら全店・全日について欠測なく計算できる。

    信頼性 0.935 で、稼働と並んで最も高い (2026-09-02)。
    """
    df = q("""SELECT hall, ratio, ev_idle FROM hall_event_crowd
              WHERE ratio IS NOT NULL AND ev_idle IS NOT NULL""")
    if df.empty:
        return ("", None, None)
    row = df[df["hall"] == hall]
    if row.empty:
        return ("", None, None)
    v = float(row["ratio"].iloc[0])
    idle = float(row["ev_idle"].iloc[0])
    # 判定の主軸は倍率でなく「イベント日に空き台があるか」。
    # 倍率は満台で頭打ちし、抽選250人の店も500人の店も同じ値で止まる。
    # 実例: アイランド秋葉原店は倍率1.00倍だが遊休0.1%で、通常日から満台。
    # 「変わらない」のではなく「増える余地が無い」(2026-09-02 ユーザー指摘)
    # 「厳しい」だけでは何が厳しいのか分からない。席が取れるかの話だと
    # 分かる言い方にする (2026-09-02 ユーザー指摘)
    if idle <= 0.02:
        lab = "座れない"
    elif idle <= 0.06:
        lab = "席取りはかなり困難"
    elif idle <= 0.15:
        lab = "席は取りにくい"
    elif idle <= 0.30:
        lab = "そこそこ座れる"
    else:
        lab = "空いていて座れる"
    return (lab, v, idle)


@st.cache_data(ttl=600)
def hall_tags(show_crowd: bool = False, crowd_kind: str = "event") -> dict:
    """店 → カードに出すピル [(文字, トーン)] の地図.

    カードごとにクエリを投げると一覧1枚で20回叩くことになる。
    一覧は複数画面にあるので、1回引いて使い回す (2026-09-02)。
    """
    out: dict = {}
    j = q("""SELECT hall, mean_setting FROM hall_juggler_stats
             WHERE n_unit_days >= 100 AND mean_setting IS NOT NULL""")
    for _, r in j.iterrows():
        lab = juggler_label(r["mean_setting"])
        if lab and lab != "ふつう":
            out.setdefault(r["hall"], []).append(
                (f"ジャグラー {lab} {r['mean_setting']:.2f}", JUG_TONE[lab]))
    # イベント日に座れるか。既定では一覧に出さない。
    # 赤いピルが並ぶと店の評価が悪く見え、「良い店を探す」画面が
    # 「行けない店の列」になる (2026-09-03 ユーザー指摘)。
    # 店舗詳細には常に出しており、一覧はチェックで表示できる
    if show_crowd and crowd_kind == "event":
        ec = q("""SELECT hall, ev_idle FROM hall_event_crowd
                  WHERE ev_idle IS NOT NULL""")
        for _, r in ec.iterrows():
            if r["ev_idle"] <= 0.02:
                out.setdefault(r["hall"], []).insert(0, ("イベント日は座れない", "cold"))
            elif r["ev_idle"] <= 0.06:
                out.setdefault(r["hall"], []).insert(0, ("イベント日は席取り困難", "cool"))
    elif show_crowd:
        # 通常日の順位を見ている画面では、イベント日の混雑は噛み合わない。
        # 普段の空き台率を使う。対象も 540店 → 1,998店 に広がる。
        # 閾値は実測の分位点 (下位5% 2.1% / 下位10% 3.8% / 下位25% 8.0%)
        nc = q("""SELECT hall, idle_rate FROM hall_util_stats
                  WHERE idle_rate IS NOT NULL AND n_unitdays >= 100""")
        for _, r in nc.iterrows():
            if r["idle_rate"] <= 0.021:
                out.setdefault(r["hall"], []).insert(0, ("普段から満台", "cold"))
            elif r["idle_rate"] <= 0.038:
                out.setdefault(r["hall"], []).insert(0, ("普段から混む", "cool"))

    # 稼働。「空いてる」側だけ出す。混んでいるのは当たり前で情報にならないが、
    # 「甘いのに空いている」は効く。実際、稼働とジャグラー予想設定は
    # r=-0.352 の負の相関があり、台日数を3,000以上に絞っても -0.408 と
    # 消えないので推定のブレではない (2026-09-02)。
    # 上位25%だと該当がほぼ出なかったので中央値未満にする
    u = q("""SELECT hall, avg_games FROM hall_util_stats
             WHERE n_unitdays >= 200 AND avg_games IS NOT NULL""")
    if not u.empty:
        thr = u["avg_games"].median()
        for _, r in u[u["avg_games"] <= thr].iterrows():
            out.setdefault(r["hall"], []).append(
                (f"空いてる {r['avg_games']:,.0f}G", ""))
    # 最近の方針の変化。「昔は良かったが今は締めている」店を避けられる。
    # 店舗詳細には出していたが一覧に無く、比較のときに見えなかった
    # (2026-09-02)。実データで -222→-338枚 のように大きく動く店がある
    ts = q("""SELECT hall, trend, recent_mai, past_mai FROM hall_style_stats
              WHERE trend IS NOT NULL AND n_days >= 20""")
    for _, r in ts.iterrows():
        if r["trend"] >= 80:
            out.setdefault(r["hall"], []).append(
                (f"最近少し出る {r['past_mai']:+,.0f}→{r['recent_mai']:+,.0f}枚", "warm"))
        elif r["trend"] <= -80:
            out.setdefault(r["hall"], []).append(
                (f"最近少し出ない {r['past_mai']:+,.0f}→{r['recent_mai']:+,.0f}枚", "cool"))

    # AT機は無効化中 (at_label 参照)。有効化したらここに足す
    return out


@st.cache_data(ttl=600)
def sweet_halls(kind: str) -> set:
    """甘め以上 (上位25%) の店の集合。ランキングの絞り込み用.

    ジャグラーと AT機 は店の方針が一致するとは限らないので別々に持つ。
    2026-09-04 に測り直したところ相関は r=-0.05 で、同期間で見ても
    前半→後半で見てもゼロだった (それぞれの自己相関は 0.91 / 0.75 と高い)。
    店は両者を完全に別管理している。まとめてはいけない。
    """
    if kind == "juggler":
        df = q("""SELECT hall, mean_setting AS v FROM hall_juggler_stats
                  WHERE n_unit_days >= 100 AND mean_setting IS NOT NULL""")
    else:
        df = q("""SELECT hall, payout AS v FROM hall_at_stats
                  WHERE n_unitdays >= 500 AND payout IS NOT NULL""")
    if df.empty:
        return set()
    thr = df["v"].quantile(0.75)
    return set(df[df["v"] >= thr]["hall"])


@st.cache_data(ttl=600)
def at_label(hall: str) -> tuple:
    """ジャグラー以外 (AT機ほか) の甘辛を返す (ラベル, トーン, 出率).

    ジャグラーは設定を読めるので予想設定を出すが、AT機は読めない。
    ただし「実際にどれだけ出ているか」は測れるので、そちらで甘辛を出す。

    ジャグラーとは別に出す。2026-09-04 の再検証 (Issue #3 項目9) では
    両者の相関は r=-0.05 で、同期間でも前半→後半でもゼロだった。
    それぞれの自己相関は 0.91 / 0.75 と高いので、指標のブレではなく
    「店が別々に決めている」と読める。

    経緯 (2026-09-02 → 09-03):
      一度は「信頼性 0.748 で基準 0.80 に届かない」として無効化した。
      しかしこの基準自体が誤りだった。信頼性は「順位が再現するか」であって
      「順位に従うといくら得か」ではない。前半で上位1割を選び後半を実測すると

          上位1割 100.4% / 全店 100.0% / 下位1割 98.7%  → 差 1.7pt

      で、1万ゲーム (3万枚投入) なら約 510枚 の差になる。実用になるので戻す。
      hall_at_stats は daily のみで計算しており、由来の偏りも解消済み。
    """
    df = q("""SELECT hall, payout FROM hall_at_stats
              WHERE n_unitdays >= 500 AND payout IS NOT NULL""")
    if df.empty:
        return ("", "", None)
    row = df[df["hall"] == hall]
    if row.empty:
        return ("", "", None)
    v = float(row["payout"].iloc[0])
    above = (df["payout"] > v).mean()
    lab = ("激甘" if above <= 0.10 else "甘め" if above <= 0.25 else
           "ふつう" if above <= 0.75 else "辛め" if above <= 0.90 else "激辛")
    return (lab, JUG_TONE[lab], v)


@st.cache_data(ttl=600)
def raw_available() -> bool:
    """生データ (units/reports) が引けるか.

    設計 (2026-09-02、ユーザー方針):
      本番は自宅PCが常時稼働し、生データ込みの本体DBを直接見る。
      有事だけクラウドの serve.db に逃がし、そこは集計のみ・詳細は無くてよい。

    新しく「生データが要る機能」を足すときは、この関数で分岐して、
    serve.db では出さないようにする。serve.db に生データを持たせようと
    すると、全国展開後に容量とメモリ (Streamlit Cloud 1GB) で行き詰まる。

    現状 serve.db は 77MB。日数で増えるのは hall_event_machine_daily だけで、
    それもイベント日 (年30日程度) に限られる。残りは店数に比例するので
    全国3,500店でも 250〜300MB の見込み。この余裕は「生データを入れない」
    という前提の上に成り立っている。
    """
    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as c:
            return bool(c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='units'"
            ).fetchone())
    except Exception:
        return False
WD_JA = "月火水木金土日"
# ナビは絵文字を外し名前を詰める。絵文字付きの長い名前だと3行に折り返し、
# 本題のカードが画面下部へ押し出されていた (2026-09-01)
# 8項目でスマホ幅3行になったのでラベルを詰めた (2026-09-02)。
# 合計幅 740px → 2行に収まる長さへ
# 地方 → 都道府県。都県の選択を2段のチップにするため (2026-09-02)。
# selectbox は開くと本体が検索欄に変わり、同じ場所を再タップしても閉じない
# (baseweb の仕様で st.selectbox から変えられない)。47県をチップで
# 一列に並べると10行近くなるので、地方で一段挟む。
REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "中部": ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
             "岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県"],
    "四国": ["徳島県", "香川県", "愛媛県", "高知県"],
    "九州・沖縄": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県",
                   "鹿児島県", "沖縄県"],
}
PREF_REGION = {p: r for r, ps in REGIONS.items() for p in ps}

PAGES = ["ランキング", "本日", "イベント", "店舗", "ジャグラー", "傾向"]
# 「検証」「サイト」はナビから外し「傾向」のタブに統合 (2026-09-04 レイアウト見直し 3)。
# 8項目は 390px 幅で2行になり、毎ページ先頭 174px を占めていた。3つとも読み物で
# 利用は主要画面の1/3 (傾向113 / 検証99 / サイト103 セッション)。6項目なら1行に届く。
# プログラム的に「検証」「サイト」へ遷移する箇所は無い (grep 確認済み)。
# 最初に開く画面。「本日」は今日イベント日の店 (全体の1割) しか出さないので、
# 自分の店が無い＝掲載されていない、と読まれていた。日付に依存せず全店が
# 対象で、上位1割を選ぶと実測 +150枚/台日 の差が出るランキングを既定にする
# (2026-09-03 ユーザー判断)
HOME_PAGE = "ランキング"
CONTACT = "info@hallscore.com"

st.set_page_config(page_title="ホールスコア | スロット店舗データ", page_icon="🦉", layout="centered",
                   initial_sidebar_state="collapsed")


@st.cache_data(ttl=600)
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()


@st.cache_data(ttl=600)
def _table_exists(name: str) -> bool:
    """テーブルの有無。engine を回す前の DB でも画面が落ちないようにする.

    新しいテーブルを足した直後は、配信用DB (serve.db) にまだ無い状態が
    ありうる。無いときは黙ってその節を出さない (2026-09-04)。
    """
    return not q("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                 (name,)).empty


@st.cache_data(ttl=3600)
def geocode_cands(text: str) -> list[tuple[str, float, float]]:
    """候補リスト (タイトル, lat, lon) をスコア順で返す。曖昧な地名はUI側で選ばせる."""
    full = _geocode_all(text)
    seen: set[str] = set()
    out = []
    for t, lat, lon in full:
        if t not in seen:
            seen.add(t)
            out.append((t, lat, lon))
        if len(out) >= 8:
            break
    return out


def _geocode_all(text: str) -> list[tuple[str, float, float]]:
    """国土地理院で住所→座標。候補は関連度順でない (「川崎」の先頭が北海道になる) ため、
    サービス提供エリア (登録店の重心) に最も近い候補を採用する."""
    url = "https://msearch.gsi.go.jp/address-search/AddressSearch?q=" + urllib.parse.quote(text)
    try:
        body = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "hallscore-app/1.0"}), timeout=10).read()
        cands = json.loads(body)
        if not cands:
            return []
        # ① サービス提供エリア (登録店のbbox+余白) 内に絞る
        hp = halls.dropna(subset=["lat", "lon"])
        la, lo = hp["lat"], hp["lon"]
        box = (la.min() - 0.3, la.max() + 0.3, lo.min() - 0.3, lo.max() + 0.3)
        in_box = [x for x in cands
                  if box[0] <= x["geometry"]["coordinates"][1] <= box[1]
                  and box[2] <= x["geometry"]["coordinates"][0] <= box[3]] or cands
        # ② 名前一致を優先しつつ、最寄りの登録店に近い候補を選ぶ
        #    (「新宿」が静岡の新宿に飛ぶ等の同名地名の誤爆対策。店のない場所の予測は無意味)
        pts = list(zip(la.tolist(), lo.tolist()))

        def near_hall(x) -> float:
            xlon, xlat = x["geometry"]["coordinates"]
            return min((xlat - a) ** 2 + (xlon - b) ** 2 for a, b in pts)

        def score(x):
            t = x["properties"].get("title", "")
            landmark = 0 if t.endswith(("駅", "市役所", "区役所", "町役場")) else 1
            return (0 if text in t else 1, landmark, len(t), near_hall(x))
        in_box.sort(key=score)
        return [(x["properties"].get("title", "?"),
                 x["geometry"]["coordinates"][1], x["geometry"]["coordinates"][0])
                for x in in_box]
    except Exception:
        return []


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fmt_date(s: str) -> str:
    d = dt.date.fromisoformat(s)
    return f"{d.month}/{d.day}({WD_JA[d.weekday()]})"


def fmt_stars(val: float) -> str:
    """連続値 (1.0〜5.0) → 「★★★★☆ 4.3」形式 (数値併記必須、2026-08-31 ユーザー指定)."""
    v = max(1.0, min(5.0, val))
    n = int(v)  # 切り捨て (★の数が数値を上回らないように)
    return "★" * n + "☆" * (5 - n) + f" {v:.1f}"


# ---------- 利用状況の計測 (2026-09-01) ----------
# Streamlit は単一ページアプリで、画面を切り替えても URL が変わらない。そのため
# 一般的なアクセス解析では「どの画面が使われたか」が分からない。課金の線引きを
# 実データで決めるために、画面と操作だけをアプリ内で記録する。
#
# 記録するもの : 画面名・操作の種類・対象(店名など)・時刻・セッションID
# 記録しないもの: IPアドレス、ユーザーエージェント、その他個人を特定しうる情報
#   (セッションIDは起動ごとの乱数で個人には紐づかない)
#
# 配信用DBとは別ファイルにする (serve.db に混ぜない)。書き込みに失敗しても
# アプリは止めない — 計測のために本体が落ちるのは本末転倒なので握りつぶす。
USAGE_DB = ROOT / "data" / "usage.db"


def _usage_session() -> str:
    if "_uid" not in st.session_state:
        import uuid
        st.session_state["_uid"] = uuid.uuid4().hex[:16]
    return st.session_state["_uid"]


def track(kind: str, target: str = "") -> None:
    try:
        USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(USAGE_DB, timeout=2)
        con.execute("""CREATE TABLE IF NOT EXISTS events (
            ts TEXT DEFAULT (datetime('now','localtime')),
            session TEXT, kind TEXT, target TEXT)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts)")
        con.execute("INSERT INTO events (session, kind, target) VALUES (?,?,?)",
                    (_usage_session(), kind, target[:120]))
        con.commit()
        con.close()
    except Exception:
        pass        # 計測の失敗でアプリを止めない


def track_once(kind: str, target: str = "") -> None:
    """同一セッション内で同じ組み合わせは1回だけ記録する (再実行のたびの重複を防ぐ)."""
    key = f"_tk_{kind}_{target}"
    if not st.session_state.get(key):
        st.session_state[key] = True
        track(kind, target)


# ---------- 根拠の強さラベル (2026-09-01 の検証結果に基づく) ----------
# 前向き検証 (前半で学習→後半で評価) を通ったものだけを「検証済み」と呼ぶ。
# 通らなかったもの・決着しなかったものは、数字は出すがラベルで区別する。
#   店×イベント日 … OOS t=+9.87  (通常日より+63枚/台)          → 検証済み
#   機種の地力     … OOS t=+13.38 (上位2割で+187枚/台)          → 検証済み
#   機種のイベント上乗せ … train↔test 相関 r=+0.046             → 参考
#   台番 (末尾)    … 0.5pt を注入しても回収できず = 検出力不足   → 決着せず
#   台番 (角・位置)… +0.20pt 出るが回転数をそろえると -0.11pt   → 決着せず
EVIDENCE = {
    "verified": ("◎", "検証済み",
                 "古いほうのデータだけで見立てを作り、新しいほうのデータで"
                 "本当に出ていたかを確かめています"),
    "weak": ("○", "参考",
             "過去の集計では出ていますが、次の期間にも続くかは確認できていません"),
    # 「○ 参考」= まだ次の期間で確かめていない
    # 「▲ 参考・裏付けなし」= 確かめた結果、続かなかった
    # 両方を単に「参考」にすると、この2つが同じ顔になる (2026-09-02)
    "occult": ("▲", "参考・裏付けなし",
               "検証しましたが、次の期間でも続くとは言えませんでした。"
               "過去にそうだったという記録として置いています"),
    "small": ("○", "参考・効果は小さめ",
              "前半で選んだ台が後半でもやや良い、という関係は確認できました。"
              "ただし差はごくわずかです"),
}


def short(name: str, n: int = 20) -> str:
    """一覧用に機種名を詰める。長い機種名が折り返しを汚すため (2026-09-01)."""
    name = str(name)
    return name if len(name) <= n else name[:n] + "…"


def evidence_badge(level: str, inline: bool = False) -> str:
    mark, label, _ = EVIDENCE[level]
    return f"{mark} {label}" if inline else f"**{mark} {label}**"


def evidence_note(level: str) -> str:
    return EVIDENCE[level][2]


_CARD_SEQ = {"n": 0}


def _ci() -> int:
    """カードごとに一意なキーを作る。st.container に key を付けると
    st-key-<key> というクラスが出るので、CSS から狙える (2026-09-01)。
    Streamlit 1.56 の枠付きコンテナは汎用の stVerticalBlock で、
    専用の data-testid を持たないため、これが唯一の確実な手段。"""
    _CARD_SEQ["n"] += 1
    return _CARD_SEQ["n"]


def _interp(x: float, pts: list[tuple[float, float]]) -> float:
    """折れ線補間 (pts は昇順)."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def conf_by_n(n_unit_days: float, drift=None) -> tuple[float, str]:
    """サンプル量×一貫性→信頼度の数値 (1.0〜5.0) と注記。前後半の差が大きい店は -1.0."""
    v = _interp(n_unit_days, [(20, 1.0), (50, 2.0), (150, 3.0), (300, 4.0), (600, 5.0)])
    note = ""
    if drift is not None and not pd.isna(drift) and abs(drift) > 0.4:
        v = max(1.0, v - 1.0)
        note = "⚠️ 直近のみ好調" if drift > 0 else "⚠️ 直近は下降中"
    return v, note


def stars_by_n(n_unit_days: float, drift=None) -> tuple[str, str]:
    v, note = conf_by_n(n_unit_days, drift)
    return fmt_stars(v), note


def conf_by_p(uplift, p, concentrated=None) -> float | None:
    """実績の有意性×一貫性→信頼度の数値。実績なし/マイナスは None."""
    if uplift is None or pd.isna(uplift) or uplift <= 0:
        return None
    if p is None or pd.isna(p):
        return 1.0
    v = _interp(-math.log10(max(p, 1e-4)), [(0, 1.0), (1, 2.3), (2, 3.7), (3, 5.0)])
    if concentrated == 1:
        v = min(v, 4.0)
    return v


def stars_by_p(uplift, p, concentrated=None) -> str:
    if uplift is None or pd.isna(uplift):
        return ""
    if uplift <= 0:
        return "—"
    return fmt_stars(conf_by_p(uplift, p, concentrated) or 1.0)


@st.cache_data(ttl=600)
def juggler_units(hall: str, machine: str) -> pd.DataFrame:
    """ジャグラーの台別スコア。同一店×同一機種の平均からの推定設定の偏差。

    ジャグラーは1Gあたりの分散が小さく設定を読む精度が高いこと、島移動が少なく
    台番の同一性が保てることから、この機種群でだけ台別の傾向を出せる (2026-09-01 検証)。
    店・機種の違いと島の再編成は engine 側で統制済み。
    """
    return q("""SELECT unit_no, dev_setting, n_days, rank_in_machine, n_units
                FROM juggler_unit_score WHERE hall=? AND machine=?
                ORDER BY rank_in_machine""", (hall, machine))


# ---------- 前向き検証ベースの信頼度 (2026-09-01、シャッフルp値から移行) ----------
# シャッフルp値は学習データ内の検定で「次も効くか」に答えない。期間を前後半に割り、
# 前半で見えた uplift が後半にも出たかで判定する。実測 (234組):
#   両方プラス 147組 → 後半 +0.875pt / 片方だけ 63組 → -0.150pt / 両方マイナス 24組 → -0.638pt
# 前後半に割れるのは 552組中 234組 (42.4%)。残りは★を出さず「検証待ち」と明示する。
OOS_LABEL = {
    # ラベルは打ち手が知りたいこと (この数字は信じてよいか) を言う。
    # 「検証済み」は手続きの名前でこちら側の都合。
    # 「信頼できる」だけだと「勝てる」と読まれるので、対象をデータに限定する。
    "both": ("信頼度", "古いデータと新しいデータの両方で出ていました"),
    "one": ("ムラがある", "片方の時期でしか出ていませんでした"),
    "neither": ("出ていない", "どちらの時期も出ていませんでした"),
}


def stars_by_oos(state, test_pt) -> tuple[str, str, str]:
    """(★表示, 短いラベル, 注記) を返す。検証待ちは ★空文字。

    ★の大きさは「後半で実際に出た uplift」で決める。見かけの実績ではなく
    答え合わせ済みの数字を使うことで、◎ラベルと数字の出どころを一致させる。
    """
    if state is None or (isinstance(state, float) and pd.isna(state)):
        return "", "データ不足", "答え合わせに必要なイベント日がまだ足りません (10日必要)"
    label, note = OOS_LABEL.get(state, ("検証待ち", ""))
    if state == "both":
        v = _interp(float(test_pt or 0), [(0.0, 3.0), (0.5, 3.7), (1.0, 4.3), (2.0, 5.0)])
    elif state == "one":
        v = 2.0
    else:
        v = 1.0
    return fmt_stars(v), label, note


def _level(v: float, hi: float, mid: float) -> str | None:
    """効果量→強度。高=バッジ表示対象、中=詳細のみ、低/None=特徴なし."""
    if v >= hi:
        return "高"
    if v >= mid:
        return "中"
    return None


@st.cache_data(ttl=600)
def recommend_units(hall: str, machine: str, rules: list) -> pd.DataFrame:
    """店の癖から「その機種のどの台を狙うか」を順位づける。
    台ごとの過去成績は引き(分散)に埋もれるので使わず、末尾・並びの位置という
    店の設定配分の癖で評価する。ルールごとに癖が違うため、選んだ日のルールを優先。"""
    # 直近のイベント日でその機種が設置されていた台番 (最新の並びを使う)
    # 生データ (units) ではなく事前集計を引く。serve.db から生データを外すため (2026-09-01)
    units = q("""SELECT unit_no FROM hall_machine_units
                 WHERE hall=? AND machine=? ORDER BY unit_no""", (hall, machine))
    if units.empty:
        return pd.DataFrame()
    nos = units["unit_no"].dropna().astype(int).tolist()
    if len(nos) < 2:
        return pd.DataFrame()

    # 末尾の癖: 選んだ日のルール優先、無ければ全イベント平均
    tail_mai: dict[int, float] = {}
    src = ""
    if rules:
        ph = ",".join("?" * len(rules))
        d = q(f"""SELECT tail, AVG(mai) AS mai FROM rule_tail_stats
                  WHERE hall=? AND rule IN ({ph}) GROUP BY tail""", (hall, *rules))
        if not d.empty:
            tail_mai = dict(zip(d["tail"], d["mai"]))
            src = "・".join(rules) + "の実績"
    if not tail_mai:
        d = q("SELECT tail, ev_mai AS mai FROM hall_tail_stats WHERE hall=? AND ev_mai IS NOT NULL",
              (hall,))
        tail_mai = dict(zip(d["tail"], d["mai"]))
        src = "全イベント日の平均"
    tbase = (sum(tail_mai.values()) / len(tail_mai)) if tail_mai else 0.0

    # 並びの位置の癖 (角=0, 端から2番目=1 …)
    pos_mai: dict[int, float] = {}
    if rules:
        ph = ",".join("?" * len(rules))
        d = q(f"""SELECT pos, AVG(mai) AS mai FROM rule_pos_stats
                  WHERE hall=? AND rule IN ({ph}) GROUP BY pos""", (hall, *rules))
        pos_mai = dict(zip(d["pos"], d["mai"]))
    if not pos_mai:
        d = q("SELECT pos, mai FROM hall_edge_pos WHERE hall=? AND kind='event'", (hall,))
        pos_mai = dict(zip(d["pos"], d["mai"]))
    pbase = (sum(pos_mai.values()) / len(pos_mai)) if pos_mai else 0.0

    # 機種の並びを連番ブロックに分けて各台の「端からの距離」を出す (engine と同じ定義)
    blocks, cur_b = [], [nos[0]]
    for n in nos[1:]:
        if n - cur_b[-1] <= 3:
            cur_b.append(n)
        else:
            blocks.append(cur_b)
            cur_b = [n]
    blocks.append(cur_b)

    rows = []
    for b in blocks:
        L = len(b)
        for i, no in enumerate(b):
            pos = min(min(i, L - 1 - i), 4) if L >= 3 else 0
            t_sc = tail_mai.get(no % 10, tbase) - tbase
            p_sc = pos_mai.get(pos, pbase) - pbase
            why = []
            # 評価語 (甘い/不利) でなく過去形の事実で書く (2026-09-04, Issue #3)。
            # 店ごとに検定したが末尾・角に固める店は見つからなかった。
            # 「出ていた」は事実、「甘い」は次も出る含意になる
            if t_sc >= 50:
                why.append(f"末尾{no % 10}が出ていた ({t_sc:+,.0f}枚)")
            elif t_sc <= -50:
                why.append(f"末尾{no % 10}は出ていなかった ({t_sc:+,.0f}枚)")
            if L >= 3 and pos == 0 and p_sc >= 30:
                why.append(f"端が出ていた ({p_sc:+,.0f}枚)")
            elif p_sc >= 30:
                why.append(f"端から{pos + 1}番目が甘い ({p_sc:+,.0f}枚)")
            rows.append({"台番": no, "この店の平均より": round(t_sc + p_sc),
                         "根拠": " / ".join(why) or "特筆なし"})
    df = pd.DataFrame(rows).sort_values("この店の平均より", ascending=False).head(8)
    df.attrs["src"] = src
    return df


@st.cache_data(ttl=600)
def hall_traits() -> dict:
    """店ごとの特徴を {hall: {特徴名: (強度, 説明)}} で返す。
    強度: 高 (バッジ表示) / 中 (店舗情報のサマリーのみ)。閾値未満は載せない."""
    tr: dict[str, dict] = {}

    def put(hall, name, lv, desc):
        if lv:
            tr.setdefault(hall, {})[name] = (lv, desc)

    # 末尾 (店内で突出した末尾)
    tdf = q("""SELECT hall, tail, ev_mai FROM hall_tail_stats
               WHERE ev_mai IS NOT NULL""")
    for hall, g in tdf.groupby("hall"):
        if len(g) < 8:
            continue
        base = g["ev_mai"].mean()
        top = g.sort_values("ev_mai", ascending=False).iloc[0]
        if top["ev_mai"] > 0:
            put(hall, f"末尾{int(top['tail'])}", _level(top["ev_mai"] - base, 200, 100),
                f"末尾{int(top['tail'])}が店平均より{top['ev_mai']-base:+,.0f}枚")
    # 角
    for hall, d in q("""SELECT hall, edge_mai - mid_mai AS d FROM hall_edge_stats
                        WHERE kind='event'""").itertuples(index=False):
        put(hall, "角(並びの端)", _level(d, 250, 120), f"端が中より{d:+,.0f}枚")
    # 連続投入
    for hall, d in q("""SELECT hall, p_cond - p_base AS d FROM hall_neighbor_stats
                        WHERE kind='event'""").itertuples(index=False):
        put(hall, "連続投入", _level(d, 0.15, 0.08), f"隣も出ていた率が全体より{d:+.0%}")
    # 曜日
    wdf = q("SELECT hall, weekday, mai FROM hall_weekday_stats")
    for hall, g in wdf.groupby("hall"):
        if len(g) < 5:
            continue
        base = g["mai"].mean()
        top = g.sort_values("mai", ascending=False).iloc[0]
        if top["mai"] > 0:
            put(hall, f"{'月火水木金土日'[int(top['weekday'])]}曜",
                _level(top["mai"] - base, 300, 150), f"店平均より{top['mai']-base:+,.0f}枚")
    # 機種の新旧
    adf = q("SELECT hall, bucket, mai FROM hall_age_stats")
    for hall, g in adf.groupby("hall"):
        mp = dict(zip(g["bucket"], g["mai"]))
        if all(k in mp for k in ("new", "semi", "mature")) and mp["semi"] > 0:
            d = min(mp["semi"] - mp["new"], mp["semi"] - mp["mature"])
            put(hall, "準新台", _level(d, 150, 80), f"準新台が他区分より{d:+,.0f}枚")
        if "new" in mp and "mature" in mp:
            put(hall, "新台", _level(mp["new"] - mp["mature"], 150, 80),
                f"新台が定番より{mp['new']-mp['mature']:+,.0f}枚")
            put(hall, "定番機種", _level(mp["mature"] - mp["new"], 300, 150),
                f"定番が新台より{mp['mature']-mp['new']:+,.0f}枚")
    # 機種ジャンル (ジャグラー vs AT)
    gdf = q("SELECT hall, genre, mai FROM hall_genre_stats")
    for hall, g in gdf.groupby("hall"):
        mp = dict(zip(g["genre"], g["mai"]))
        if "ジャグラー" in mp and "AT・スマスロ" in mp:
            d = mp["ジャグラー"] - mp["AT・スマスロ"]
            put(hall, "ジャグラー", _level(d, 300, 150), f"ジャグラーがATより{d:+,.0f}枚")
            put(hall, "AT機", _level(-d, 300, 150), f"ATがジャグラーより{-d:+,.0f}枚")
    return tr


@st.cache_data(ttl=600)
def hall_badges() -> dict:
    """一覧カード用バッジ: 強度「高」の特徴のみ (ノイズを減らし、付いたら本物という意味に)."""
    # 絵文字は使わない。低彩度でまとめた配色の上に端末のカラー絵文字が乗ると、
    # 画面で最も彩度が高いのがバッジになり、店名や数字より目立ってしまう。
    # 区別は丸ピルの形と位置で足りる (2026-09-01 デザインレビュー)
    # 末尾・角・連続投入は過去形の事実表記にする (2026-09-04, Issue #3 Phase A)。
    # 店ごとに検定して「固める店」は見つからなかった (設定6級で 515店中ゼロ)。
    # バッジは差枚 +200枚 などの閾値で付いており検定は通っていない。
    # 数字は事実として残すが、「末尾7」だけだと次も出る含意になるので
    # 「出ていた」を付けて、過去の記録であることを語で示す。
    # 内部キー (name) は分岐で使うので変えず、表示ラベルだけ変える
    out: dict[str, list] = {}
    for hall, traits in hall_traits().items():
        for name, (lv, _d) in traits.items():
            if lv != "高":
                continue
            out.setdefault(hall, []).append(trait_label(name))
    return out


def trait_label(name: str) -> str:
    """特徴の内部キー → 画面に出す語。バッジと特徴まとめの両方で使う.

    末尾・角・連続投入は過去形の事実表記にする (2026-09-04, Issue #3 Phase A)。
    店ごとに検定して「固める店」は見つからなかった (設定6級で 515店中ゼロ)。
    これらは差枚 +200枚 などの閾値で付いており検定は通っていない。
    数字は事実として残すが、「末尾7」だけだと次も出る含意になるので
    「出ていた」を付けて過去の記録であることを語で示す。
    内部キーは分岐で使うので変えず、表示だけここで変える。
    """
    if name.startswith("末尾"):
        return f"{name}が出ていた"
    return {"角(並びの端)": "端が出ていた", "連続投入": "隣同士で出ていた"}.get(name, name)


@st.cache_data(ttl=600)
def honmei_halls() -> set:
    """◎本命級 (+300枚/台×5日以上) の機種を持つ店の集合。絞り込みフィルタ用."""
    df = q("""SELECT DISTINCT hall FROM machine_event_score
              WHERE uplift_mai >= 300 AND n_days >= 5""")
    return set(df["hall"])


@st.cache_data(ttl=1800)
def trend_qa() -> list[dict]:
    """全店集計からデータ駆動のQ&Aを生成 (俗説をデータで検証)。夜間cronで最新化。
    非縮退・games>=100 の台あたり平均差枚 (通常日=daily/control 中心)。"""
    con = sqlite3.connect(DB)
    # 事前集計 (engine の global_stats) を読む。units を直接集計しないので配信DBでも動く
    G = {k: v for k, v in con.execute("SELECT key, value FROM global_stats")}

    qa = []
    # 平日 vs 土日。
    # 🚨 差枚で比べると「平日が20枚良い」と出るが、これは土日が平日の1.35倍
    #   回るせい。出率で見ると差はない。G数がそろわない比較は出率でしか語れない
    we, wd = G.get("we_payout"), G.get("wd_payout")
    weg, wdg = G.get("we_games"), G.get("wd_games")
    if we is not None and wd is not None:
        _d = wd - we
        qa.append({"q": "平日と土日、どっちが設定入る?",
                   "a": ("ほとんど変わりません" if abs(_d) < 0.05 else
                         f"{'平日' if _d > 0 else '土日'}がわずかに良い"),
                   "d": f"出玉率は平日 {wd:.2f}% / 土日 {we:.2f}%（{_d:+.2f}ポイント）。"
                        f"差枚で比べると平日のほうが良く見えますが、それは土日が"
                        f"{weg/wdg:.2f}倍多く回るためです"
                        f"（土日 {weg:,.0f}G / 平日 {wdg:,.0f}G）。"
                        "回れば投入も増えるので、同じ出玉率でも差枚は大きく振れます。"
                        "店ごとの差のほうがはるかに大きいので、店舗情報の曜日傾向で確認を。"})
    # イベント日 vs 通常日。ここも差枚では逆に見える (イベント日は1.18倍回る)
    ev, nm = G.get("ev_payout"), G.get("nm_payout")
    evg, nmg = G.get("ev_games"), G.get("nm_games")
    if ev is not None and nm is not None:
        qa.append({"q": "イベント日は本当に出る?",
                   "a": f"通常日より良いですが、全店平均では小さい（{ev - nm:+.2f}ポイント）",
                   "d": f"出玉率はイベント日 {ev:.2f}% / 通常日 {nm:.2f}%。"
                        f"イベント日は{evg/nmg:.2f}倍多く回る（{evg:,.0f}G / {nmg:,.0f}G）ので、"
                        "差枚で比べると逆に悪く見えます。回るほど投入も増えるためで、"
                        "出玉率で見るのが正しい比べ方です。"
                        "全店平均が小さいだけで「出る店・機種」は確実にあるので、"
                        "狙い目イベントや店舗情報で個別に見るのが正解。"})
    # 給料日・年金日 (祝日は sqlite で判定不可のため月内の日で回答)
    m25 = G.get("d25_mai")
    pen = G.get("pen_mai")
    pen_p, m25_p = G.get("pen_payout"), G.get("d25_payout")
    if pen_p is not None and m25_p is not None:
        qa.append({"q": "給料日や年金日は狙い目?",
                   "a": "狙い目とは言えません",
                   "d": f"出玉率は年金日(偶数月15日) {pen_p:.2f}% / 給料日25日 {m25_p:.2f}% / "
                        f"通常日 {nm:.2f}%。年金日は少し高く見えますが、"
                        "同じ店・同じ曜日の中だけで比べ直すと差は消えます"
                        "（-0.05ポイント、統計的にも有意でない）。"
                        "年金日は普段より1.24倍多く回る日なので、"
                        "混雑の影響を取り除くと残るものがありませんでした。"})
    # 末尾
    tail = G.get("tail_spread")
    # 「特定末尾を狙う店は実在する」は撤回 (2026-09-04, Issue #3 Phase A)。
    # 店ごとに検定した。出率では3台の高設定を拾えない (陽性対照 0%) ので、
    # ジャグラーの推定設定で「同じ末尾に設定6級を3台」を検出率90%の方法で探し、
    # 515店中ゼロ (偶然の期待26店を大きく下回る z=-5.2)。設定4級は1日データでは
    # どの方式でも判別できない (合成データで p_ge4 0.52、1σ) ため不明のまま。
    qa.append({"q": "末尾(台番の下1桁)は関係ある?",
               "a": "全店では差がなく、末尾に固める店も見つかりません",
               "d": f"全店平均の末尾差は {tail:+,.0f}枚程度。店ごとにも調べました。"
                    "ジャグラーの推定設定を使い、「同じ末尾に高設定を3台以上」入れている店を"
                    "探しましたが、515店の中にありませんでした"
                    "（この方法は人工的に癖を埋め込むと9割拾えるので、見落としではありません）。"
                    "ただし判別できるのは設定6クラスまでで、設定4程度の弱い癖は"
                    "1日分のデータでは見分けられません。"
                    "店舗情報の『末尾別の出方』は事実の記録として残していますが、"
                    "次に狙う根拠にはならないと考えてください。"})
    # 角台
    edge = con.execute("""SELECT AVG(edge_mai-mid_mai) FROM hall_edge_stats WHERE kind='event'""").fetchone()[0]
    # 「端に高設定を固める店は個別に存在する」は撤回 (2026-09-04, Issue #3 Phase A)。
    # 店ごとに「その日、端と中の差が偶然より大きいか」を店内シャッフルで判定したところ、
    # 有意店は 5/506 で偶然の期待 25 店を大きく下回った (z=-4.1)。癖が無いのでなく
    # 店が端を意図的に均一にしていると読める。端は G数も少なく座られにくい。
    edge_msg = ("むしろ端は中より不利" if edge < -30 else
                "全店平均では中の台と大差なし" if edge <= 30 else
                "全店平均では端がやや甘い")
    qa.append({"q": "角台(島の端)は狙い目?",
               "a": edge_msg + "。端に固める店も見つかりません",
               "d": f"イベント日の『機種の並びの端 − 中』の平均差は {round(edge):+,.0f}枚。"
                    "店ごとにも調べましたが、端に偏らせている店は偶然の範囲より"
                    "むしろ少なく、多くの店は端を特別扱いしていません。"
                    "端の台は回転数も少なめで、座られにくい席です。"
                    "なお、ここでいう「端」は機種の並びの端で、島の物理的な角とは"
                    "9割以上一致しません（台番の飛びと機種の切り替わりの一致率 5.6%）。"})
    # 新台
    new = (G.get("new_mai"), G.get("mature_mai"))
    # 「新台に入れる店もある」は撤回した (2026-09-04, Issue #3 項目6)。
    # 出率でも新台は -0.91pt 辛く、店別に「新台が甘い店」を前半で選んでも
    # 後半では再現しなかった (r=+0.073、陰性対照 +0.002±0.084)。
    # 個別例を挙げていたが、あれはただの当たりを拾っていた
    qa.append({"q": "新台は甘い?", "a": "逆です。新台ほど辛い",
               "d": f"全店では導入0-14日 {round(new[0]):+,.0f}枚 → 121日以上 "
                    f"{round(new[1]):+,.0f}枚と新台ほど悪い"
                    "（話題性で客が座るので店は締める）。出玉率で見ても "
                    "0-14日 97.24% → 121日以上 98.15% と同じ向きです。"
                    "「新台に高設定を入れる店」を探しましたが、前半のデータで見つけた店は"
                    "後半では再現しませんでした。新台が入った日そのものも狙い目ではありません。"})

    # ── ここから 2026-09-02 追加。当日の検証で分かったことを載せる ──
    # 「傾向」は検証済みの事実を並べる場所なので、読み物として一番伸びる

    # ジャグラーの予想設定は当たっているのか。
    # 実差枚は juggler_daily から直接取る。hall_payout_stats は daily のみに
    # 変えた直後で対象が23店しかなく、比較に足りない (2026-09-02)
    row = con.execute("""
        SELECT COUNT(*) FROM hall_juggler_stats WHERE n_unit_days >= 100""").fetchone()
    if row and row[0] >= 100:
        hi, lo = con.execute("""
            SELECT AVG(CASE WHEN j.mean_setting >= t.hi THEN d.mai END),
                   AVG(CASE WHEN j.mean_setting <= t.lo THEN d.mai END)
            FROM hall_juggler_stats j
            JOIN hall_juggler_mai d ON d.hall = j.hall,
                 (SELECT AVG(mean_setting)+0.1 hi, AVG(mean_setting)-0.1 lo
                  FROM hall_juggler_stats WHERE n_unit_days >= 100) t
            WHERE j.n_unit_days >= 100""").fetchone()
        if hi is not None and lo is not None:
            qa.append({
                "q": "ジャグラーの「予想設定」は当たってる?",
                "a": f"当たっている。予想が高い店のジャグラーは "
                     f"1台1日あたり {round(hi - lo):+,.0f}枚 多く出ている",
                "d": "BIG/REG の出方から1台ずつ推定した設定を、店ごとに平均したものです。"
                     f"予想が高めの店と低めの店を比べると、実際の差枚が "
                     f"{round(hi - lo):+,.0f}枚/台日ちがいます。"
                     "※ここでの枚数は店どうしを比べるための相対値です。"
                     "打った人の平均収支とは一致しません"
                     "（よく回った台ほど差枚が大きく出るため、この集計は上振れします）。"})

    # ジャグラーが甘い店は他の機種も甘いのか
    jr = con.execute("""
        SELECT AVG(CASE WHEN j.mean_setting >= t.hi THEN a.payout END),
               AVG(CASE WHEN j.mean_setting <= t.lo THEN a.payout END),
               COUNT(*)
        FROM hall_juggler_stats j
        JOIN hall_at_stats a ON a.hall = j.hall AND a.n_unitdays >= 500,
             (SELECT AVG(mean_setting)+0.1 hi, AVG(mean_setting)-0.1 lo
              FROM hall_juggler_stats WHERE n_unit_days >= 100) t
        WHERE j.n_unit_days >= 100""").fetchone()
    if jr and jr[0] is not None and jr[1] is not None and jr[2] >= 50:
        qa.append({
            "q": "ジャグラーが甘い店は、他の機種も甘い?",
            "a": f"同じ方向に寄るが、差は小さい ({jr[0] - jr[1]:+.2f}ポイント)",
            "d": f"ジャグラーが甘めの店のジャグラー以外の出玉率 {jr[0]:.2f}%、"
                 f"辛めの店は {jr[1]:.2f}%。同じ方向には動きますが、"
                 "「ジャグラーが甘い＝全部甘い」ではありません。"
                 "ジャグラーは辛いのにAT機は甘い、という店も実在します。"})

    # 旧イベント日はガセなのか
    rl = con.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN oos_state='both' THEN 1 ELSE 0 END),
               SUM(CASE WHEN uplift_mai <= 0 THEN 1 ELSE 0 END)
        FROM hall_rule_stats WHERE uplift_mai IS NOT NULL""").fetchone()
    if rl and rl[0] >= 100:
        n, ok, bad = rl
        qa.append({
            "q": "店が公表している「旧イベント日」はガセ?",
            "a": f"{100 * (n - bad) / n:.0f}% は本物。{100 * bad / n:.0f}% は効いていない",
            "d": f"店×イベント日 {n:,}件のうち、普段より出ていないものが {bad:,}件"
                 f"({100 * bad / n:.0f}%)。残りは実際に出ています。"
                 f"さらに「古いデータで見えた効きが新しいデータでも出た」ものが {ok:,}件あり、"
                 "サイトではこれを『検証済み』として出しています。"
                 "広告として使っている日なので、店には守る理由があります。"})

    # 店選びでどれだけ変わるか
    sp = con.execute("""
        SELECT MAX(mai), MIN(mai), COUNT(*) FROM (
          SELECT mai FROM hall_payout_stats WHERE kind='normal' AND n_days >= 20
          ORDER BY mai DESC)""").fetchone()
    tp = con.execute("""
        SELECT AVG(mai) FROM (SELECT mai FROM hall_payout_stats
          WHERE kind='normal' AND n_days>=20 ORDER BY mai DESC
          LIMIT (SELECT MAX(3, COUNT(*)/10) FROM hall_payout_stats
                 WHERE kind='normal' AND n_days>=20))""").fetchone()[0]
    bt = con.execute("""
        SELECT AVG(mai) FROM (SELECT mai FROM hall_payout_stats
          WHERE kind='normal' AND n_days>=20 ORDER BY mai ASC
          LIMIT (SELECT MAX(3, COUNT(*)/10) FROM hall_payout_stats
                 WHERE kind='normal' AND n_days>=20))""").fetchone()[0]
    if tp is not None and bt is not None and sp[2] >= 20:
        qa.append({
            "q": "店選びで、どれくらい変わる?",
            "a": f"上位1割と下位1割で 1日あたり {round(tp - bt):+,.0f}枚/台 の差",
            "d": f"普段の日の出方が上位1割の店は {tp:+,.0f}枚/台、下位1割は {bt:+,.0f}枚/台"
                 f"（{sp[2]}店で集計）。どこで打っても平均すれば負けますが、"
                 "店を選ぶだけで負け幅がこれだけ変わります。"
                 "「どの台に座るか」より「どの店に行くか」のほうがはるかに効きます。"})

    # 空いている店ほど甘いのか
    ur = con.execute("""
        SELECT AVG(CASE WHEN u.avg_games <= t.lo THEN j.mean_setting END),
               AVG(CASE WHEN u.avg_games >= t.hi THEN j.mean_setting END),
               COUNT(*)
        FROM hall_util_stats u
        JOIN hall_juggler_stats j ON j.hall = u.hall AND j.n_unit_days >= 100,
             (SELECT AVG(avg_games)*0.7 lo, AVG(avg_games)*1.3 hi
              FROM hall_util_stats WHERE n_unitdays >= 200) t
        WHERE u.n_unitdays >= 200""").fetchone()
    if ur and ur[0] is not None and ur[1] is not None and ur[2] >= 100:
        qa.append({
            "q": "混んでいる店の方が設定が良い?",
            "a": f"逆。空いている店の方がジャグラーの予想設定は高い",
            "d": f"空いている店の予想設定 {ur[0]:.2f} に対し、混んでいる店は {ur[1]:.2f}。"
                 "台日数を3,000以上に絞っても関係は消えないので、推定のブレではありません。"
                 "競争の少ない店が客を繋ぎ止めるために入れている、"
                 "あるいは混む店は入れなくても客が来る、と読めます。"
                 "「甘くて空いている店」は実在するので、店舗一覧の『空いてる』の"
                 "表示と甘辛を合わせて見てください。"})

    # イベント日はどれくらい混むのか
    cr = con.execute("""
        SELECT COUNT(*), AVG(ev_idle),
               SUM(CASE WHEN ev_idle <= 0.02 THEN 1 ELSE 0 END),
               SUM(CASE WHEN ev_idle >= 0.30 THEN 1 ELSE 0 END)
        FROM hall_event_crowd WHERE ev_idle IS NOT NULL""").fetchone()
    if cr and cr[0] >= 100:
        n, avg_idle, full, empty = cr
        qa.append({
            "q": "イベント日、その店に座れる?",
            "a": f"{n}店のうち {full}店は事実上の満台。{empty}店は空きがある",
            "d": f"イベント日に「ほぼ回っていない台」の割合を見ると、平均 {avg_idle:.0%}。"
                 f"{full}店は2%未満で、朝から並んでも取れないと思ったほうがいい水準です。"
                 "抽選の人数は公開データに無いので、空き台の割合で見ています。"
                 "※回転数の比だけでは測れません。満台になると頭打ちするので、"
                 "抽選250人の店も500人の店も同じ値で止まってしまいます。"
                 "「甘いけど座れない店」と「そこそこだが座れる店」は"
                 "店舗情報で確認できます。"})

    # ── ここから 2026-09-04 追加 (Issue #3 の再検証で確定したもの) ──
    # 全日収集が済んだので「データが揃うまで保留」にしていた4問に答える。
    # いずれも答えが「無い」なので、無いことをはっきり書く。
    # 探して損をする話は、載せないと読者が勝手に探してしまう。

    # 公表日以外に隠れた熱い日はあるか
    ann_p, non_p = G.get("ann_payout"), G.get("non_payout")
    share = G.get("hidden_share")
    if ann_p is not None and non_p is not None and share is not None:
        qa.append({
            "q": "公表日以外に「隠れた熱い日」はある?",
            "a": "見つかりません。探すだけ損になります",
            "d": f"告知イベント日の出玉率 {ann_p:.2f}% に対し、それ以外の日は "
                 f"{non_p:.2f}%（{ann_p - non_p:+.2f}ポイント）。全店で均すと差は"
                 "わずかです。"
                 f"ただし『告知していない日のほうが出ている店』は {share:.0%} しか"
                 "ありません。日の割り当てが無関係なら偶然でも 50% になるはずなので、"
                 "むしろ偶然を下回っています。"
                 "曜日・日付・週のどれで見ても規則は見つからず、"
                 "前半のデータで見つけた『熱い曜日』を後半で試しても "
                 "0.13ポイント未満しか差がつきませんでした。"
                 "店は告知した日にちゃんと使っている、と読むのが素直です。"})

    # 県ごとの甘辛
    has_pref = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pref_stats'"
    ).fetchone() is not None
    pr = con.execute("""
        SELECT pref, score, n_halls, hall_sd FROM pref_stats
        ORDER BY rank""").fetchall() if has_pref else []
    if len(pr) >= 8:
        top = pr[:3]
        bot = pr[-3:]
        avg_sd = sum(r[3] for r in pr) / len(pr)
        spread = pr[0][1] - pr[-1][1]
        qa.append({
            "q": "都道府県によって甘さは違う?",
            "a": f"違いますが、県の差より店の差のほうが大きいです",
            "d": "収集した日の種類（イベント日か、その対照日か、任意の日か）・曜日・月を"
                 "そろえたうえで比べています。"
                 + "甘いほう " + " / ".join(f"{p}{s:+.2f}" for p, s, _n, _d in top)
                 + "、辛いほう " + " / ".join(f"{p}{s:+.2f}" for p, s, _n, _d in bot)
                 + f"（単位は出玉率のポイント）。上下の幅は {spread:.2f}ポイントです。"
                 f"ただし同じ県の中の店のばらつきは平均 {avg_sd:.2f}ポイントあり、"
                 "県の差より大きい。**甘い県だから安心、ではありません。**"
                 f"店数が20に満たない県は数字が安定しないので出していません（{len(pr)}県）。"})

    # 新台入替日
    nd = G.get("newday_effect")
    if nd is not None:
        qa.append({
            "q": "新台入替日は狙い目?",
            "a": "違います。普段の日と差がありません",
            "d": f"同じ店・同じ曜日・同じ告知の有無の中だけで比べると、新台が入った日の"
                 f"出玉率は {nd:+.3f}ポイント。ほぼゼロです。"
                 "『入替日はイベント日と重なるから見えないだけ』とも考えましたが、"
                 "入替日が告知イベント日である割合は 39%、それ以外の日は 41% で、"
                 "むしろ重なっていませんでした。"
                 "新台そのものが辛い（この下の『新台は甘い?』）のと合わせて、"
                 "新台まわりは狙い目になりません。"})

    # 前回出た機種は次も出るか
    # 🚨 観測値 54.4% を単独で出すと「50%より高い＝続く」と正反対に読まれる。
    #    出玉率の分布は右に歪んでいて、無相関でも 54.1% になる (順序シャッフル
    #    500回で実測、2026-09-04)。ヌルを併記しないと誤誘導になる。
    #    280万ペアあるので +0.33pt でも p=0.002 になるが、実用性は無い。
    qa.append({
        "q": "前回出ていた機種は、次も出る?",
        "a": "偶然と同じです。続きません",
        "d": "同じ店の同じ機種を時系列に並べ、前回のズレと次回のズレの関係を"
             "280万組で調べました。相関は -0.031 で、順序をばらばらにした場合の "
             "-0.032 と一致します。つまり関係はありません。"
             "『前回プラスだった機種が次回もプラスになる割合』は 54.4% ですが、"
             "**無関係でも 54.1% になります**（出玉率は大きく出る側に裾が長いため）。"
             "50% と比べて高いように見えるだけで、実質の差は 0.3ポイントです。"
             "※この検証は計算が重いため毎晩は更新していません（2026-09-04 時点）。"})

    con.close()
    return qa


def min_stars_ui(key: str) -> float:
    """信頼度の下限フィルタ (全ランキング共通)."""
    # help= のツールチップはホバー前提で、スマホではまともに押せない
    # (ユーザー実測: 20回タップしてやっと出た)。常時見える注記にする (2026-09-02)
    sel = st.radio("信頼度で絞り込み", ["すべて", "★2以上", "★3以上", "★4以上"],
                   horizontal=True, key=f"ms_{key}")
    st.caption("★は信頼度 (5段階)。データが多く、長期間安定しているほど高くなります。"
               "上げるほど確実な店だけに絞られます。")
    return {"すべて": 0.0, "★2以上": 2.0, "★3以上": 3.0, "★4以上": 4.0}[sel]


def color_mai(v) -> str:
    """枚換算の段階色: 濃緑 ≥+300枚 / 緑 ≥+100枚 / 赤 ≤-100枚 / 濃赤 ≤-300枚."""
    if v is None or pd.isna(v):
        return ""
    if v >= 300:
        return "background-color:#2e7d32;color:white"
    if v >= 100:
        return "background-color:#a5d6a7"
    if v <= -300:
        return "background-color:#c62828;color:white"
    if v <= -100:
        return "background-color:#ef9a9a"
    return ""


def color_pt(v) -> str:
    """差(pt) の段階色: 濃緑 ≥+2 / 緑 ≥+0.5 / 赤 ≤-0.5 / 濃赤 ≤-2."""
    if v is None or pd.isna(v):
        return ""
    if v >= 2:
        return "background-color:#2e7d32;color:white"
    if v >= 0.5:
        return "background-color:#a5d6a7"
    if v <= -2:
        return "background-color:#c62828;color:white"
    if v <= -0.5:
        return "background-color:#ef9a9a"
    return ""


def card_html(url: str, title: str, meta: str = "", num: str = "", num_note: str = "",
              sub: str = "", badges: list | None = None,
              tag: tuple | None = None) -> str:
    """カード1枚を HTML で組む.

    st.link_button は仕様として必ず新しいタブで開き (公式ドキュメント明記)、
    同じタブで開く設定が無い。店の詳細が別タブになり、ナビの状態も引き継がれず、
    タブ切り替えのアニメーションが挟まって遷移が不自然だった (2026-09-01 実機)。
    生の <a> なら target が無いので同一タブ遷移になり、スクロールも先頭に戻る。

    副次的に、マークダウン文字列に押し込んでいた制約 (行ごとにスタイルを当てられない、
    数字を枠で囲めない、タグをピルにできない) が全部なくなる。
    """
    import html as _h
    parts = [f'<span class="c-title">{_h.escape(title)}</span>']
    if meta:
        parts.append(f'<span class="c-meta">{_h.escape(meta)}</span>')
    # tag=(文字, トーン) か、その並び。数字があれば隣に、無ければ単独行に置く
    tg = ""
    if tag:
        tl = tag if isinstance(tag, (list, tuple)) and tag and isinstance(tag[0], (list, tuple)) else [tag]
        for t in tl:
            if not t or not t[0]:
                continue
            cls = f"c-tag c-{t[1]}" if t[1] else "c-tag"
            tg += f'<span class="{cls}">{_h.escape(str(t[0]))}</span>'
    if num:
        note = f'<span class="c-note">{_h.escape(num_note)}</span>' if num_note else ""
        parts.append(f'<span class="c-num">{_h.escape(num)}</span>{tg}{note}')
    elif tg:
        parts.append(f'<span class="c-tagline">{tg}</span>')
    if sub:
        parts.append(f'<span class="c-sub">{_h.escape(sub)}</span>')
    if badges:
        pills = "".join(f'<b>{_h.escape(str(x))}</b>' for x in badges)
        parts.append(f'<span class="c-tags">{pills}</span>')
    # Streamlit のマークダウンはリンクに target="_blank" を自動で付ける。
    # 明示的に _self を指定して同一タブ遷移にする (2026-09-01 実測で判明)
    return (f'<a class="hs-card" target="_self" href="{url}">'
            + "".join(parts) + "</a>")


def hall_url(hall: str) -> str:
    """店の詳細への URL。現在の絞り込み条件を引き継ぐ.

    本物のリンクにするとブラウザがページ遷移し、スクロール位置が先頭に戻る。
    JS で戻す方式は画面外の iframe が読み込まれず動かなかった (実機で確認、
    2026-09-01)。副次的に店ごとの URL ができ、共有・ブックマークもできる。
    """
    keep = {}
    try:
        for k in ("scope", "pref", "city", "addr", "radius"):
            if k in st.query_params:
                keep[k] = st.query_params[k]
    except Exception:
        pass
    keep["hall"] = hall
    return "?" + urllib.parse.urlencode(keep)


def back_to_list() -> None:
    st.session_state.hall_pick = None
    st.session_state.hd_search = None


halls = q("SELECT hall, pref, city, address, lat, lon, n_slot, slot_exchange_mai, "
          "open_time, entry_method, entry_time, geo_approx FROM halls")


def city_options(cities) -> list[str]:
    """市区リストに政令市の「市」集約を挿入 (川崎市川崎区… の前に 川崎市 を置く)."""
    opts: list[str] = []
    seen: set[str] = set()
    for c in sorted(set(x for x in cities if isinstance(x, str))):
        m = re.match(r"^(.+?市).+区$", c)
        if m and m.group(1) not in seen:
            opts.append(m.group(1))
            seen.add(m.group(1))
        opts.append(c)
    return opts


def city_mask(series: pd.Series, sel: str) -> pd.Series:
    """市集約 (〜市) は前方一致、それ以外は完全一致."""
    if re.fullmatch(r".+?市", sel):
        return series.str.startswith(sel).fillna(False)
    return series == sel


def scope_state(key: str = "geo") -> dict:
    """UI を描かずに、いまの設定だけを組み立てて返す (畳んでいるとき用)."""
    k = lambda n: f"sc_{n}_{key}"
    m = st.session_state.get(k("mode"), "すべて")
    sel = {"mode": m}
    if m == "都道府県":
        pr = st.session_state.get(k("pref"), "すべて")
        sel["region"] = st.session_state.get(k("region")) or PREF_REGION.get(pr, "すべて")
        sel["pref"] = pr
        sel["city"] = st.session_state.get(k("city"), "すべて")
    elif m == "近く":
        sel["addr"] = st.session_state.get(k("addr"))
        sel["radius"] = st.session_state.get(k("radius"), 10)
        sel["key"] = key
        if sel["addr"]:
            cands = geocode_cands(sel["addr"])
            if len(cands) == 1:
                sel["pt"] = (cands[0][1], cands[0][2])
            else:
                gc = st.session_state.get(f"gc_{key}")
                hit = next((c for c in cands if c[0] == gc), None)
                if hit:
                    sel["pt"] = (hit[1], hit[2])
    _sync_scope_to_url(sel)
    return sel


def scope_summary(key: str = "geo") -> str:
    """いま効いている条件を1行で返す (閉じているときの表示用)."""
    k = lambda n: f"sc_{n}_{key}"
    m = st.session_state.get(k("mode"), "すべて")
    if m == "都道府県":
        pr = st.session_state.get(k("pref"), "すべて")
        g = st.session_state.get(k("region")) or PREF_REGION.get(pr, "すべて")
        if not g or g == "すべて":
            return "すべて"
        if not pr or pr == "すべて":
            return f"{g}すべて"
        parts = [pr]
        c = st.session_state.get(k("city"))
        if c and c != "すべて":
            parts.append(c)
        return "・".join(parts)
    if m == "近く":
        a = st.session_state.get(k("addr"))
        r = st.session_state.get(k("radius"), 10)
        return f"{a} から {r}km" if a else "近く"
    return "すべて"



def scope_controls(key: str = "geo") -> dict:
    """範囲の指定 UI を1回だけ描き、選択内容を返す.

    値はウィジェットのキーで持たない。パネルを畳むとラジオが描かれず、
    Streamlit が「前回のrunに無かったウィジェットの状態」を捨てるため、
    再び開いたとき先頭 (すべて) で表示されてしまう。一覧は正しく絞られて
    いるのにチップだけ「すべて」に戻る、という食い違いが起きていた
    (2026-09-03 本番で再現)。自前の変数に持ち、index で初期値を与える。
    """
    st_key = lambda n: f"sc_{n}_{key}"          # 自前の保存先 (ウィジェットのキーではない)

    def cur(n, default):
        v = st.session_state.get(st_key(n), default)
        return v if v is not None else default

    modes = ["すべて", "都道府県", "近く"]
    if cur("mode", None) not in modes:           # 旧ラベル「県内」の残存対策
        st.session_state[st_key("mode")] = modes[0]

    # 設定し終えたら畳めるようにする。決めたあとは3段のチップが邪魔になる
    okey = f"scope_open_{key}"
    if okey not in st.session_state:
        st.session_state[okey] = cur("mode", "すべて") == "すべて"

    if not st.session_state[okey]:
        cA, cB = st.columns([4, 1])
        cA.markdown(f'<div class="scope-line">エリア: <b>{scope_summary(key)}</b></div>',
                    unsafe_allow_html=True)
        if cB.button("変更", key=f"sopen_{key}"):
            st.session_state[okey] = True
            st.rerun()
        return scope_state(key)

    avail = sorted(halls["pref"].dropna().unique())
    mode = st.radio("エリア", modes, index=modes.index(cur("mode", "すべて")),
                    horizontal=True)
    st.session_state[st_key("mode")] = mode
    sel = {"mode": mode}
    if mode != "すべて":
        track_once(f"scope_{mode}", key)

    if mode == "都道府県":
        # 地方・都県のどちらにも「すべて」を置く (2026-09-02)
        regs = ["すべて"] + [r for r in REGIONS if any(p in avail for p in REGIONS[r])]
        cur_reg = cur("region", None)
        if cur_reg not in regs:
            cur_reg = PREF_REGION.get(cur("pref", None), regs[0])
            if cur_reg not in regs:
                cur_reg = regs[0]
        region = st.radio("地方", regs, index=regs.index(cur_reg), horizontal=True)
        st.session_state[st_key("region")] = region
        sel["region"] = region

        if region == "すべて":
            sel["pref"] = sel["city"] = "すべて"   # 47県のチップは並べられない
            st.session_state[st_key("pref")] = "すべて"
        else:
            prefs = ["すべて"] + [x for x in REGIONS.get(region, []) if x in avail]
            mem = st.session_state.setdefault(st_key("mem_pref"), {})
            cur_pref = cur("pref", "すべて")
            if cur_pref not in prefs:
                # その地方で前回選んでいた県に戻す。往復しただけで
                # 選択が失われるのを防ぐ (2026-09-03)
                cur_pref = mem.get(region, "すべて")
                if cur_pref not in prefs:
                    cur_pref = "すべて"
            pref = st.radio("都県", prefs, index=prefs.index(cur_pref), horizontal=True)
            st.session_state[st_key("pref")] = pref
            sel["pref"] = pref
            if pref != "すべて":
                mem[region] = pref

            if pref == "すべて":
                sel["city"] = "すべて"             # 地方まるごとなので市区は出さない
                st.session_state[st_key("city")] = "すべて"
            else:
                cities = ["すべて"] + city_options(halls[halls["pref"] == pref]["city"])
                cmem = st.session_state.setdefault(st_key("mem_city"), {})
                cur_city = cur("city", "すべて")
                if cur_city not in cities:
                    cur_city = cmem.get(pref, "すべて")
                    if cur_city not in cities:
                        cur_city = "すべて"
                # 市区は東京だけで43件あるのでチップにできない
                city = st.selectbox("市区", cities, index=cities.index(cur_city))
                st.session_state[st_key("city")] = city
                sel["city"] = city
                if city != "すべて":
                    cmem[pref] = city

    elif mode == "近く":
        addr = st.text_input("駅名・住所", value=cur("addr", "") or "",
                             placeholder="例: 川崎駅")
        st.session_state[st_key("addr")] = addr
        radius = st.slider("半径 (km)", 1, 30, int(cur("radius", 10)))
        st.session_state[st_key("radius")] = radius
        sel["addr"], sel["radius"], sel["key"] = addr, radius, key
        # 地点の確定もここで1回だけ行う。scope_apply の中でやると、一覧が
        # 2つある画面でキーが衝突して落ちる (2026-09-02 本番で発生)
        if addr:
            cands = geocode_cands(addr)
            if not cands:
                st.warning("場所を特定できませんでした")
            elif len(cands) == 1:
                sel["pt"] = (cands[0][1], cands[0][2])
            else:
                choice = st.selectbox("この場所ですか？ 候補から選んでください",
                                      [c[0] for c in cands], index=None,
                                      placeholder="候補をタップして選択",
                                      key=f"gc_{key}")
                if choice:
                    hit = next(c for c in cands if c[0] == choice)
                    sel["pt"] = (hit[1], hit[2])
                else:
                    st.info("場所の候補から選ぶと、その周辺の店が表示されます")
                    _sync_scope_to_url(sel)
                    st.stop()
    if st.button("この条件で閉じる", key=f"sclose_{key}"):
        st.session_state[okey] = False
        st.rerun()
    _sync_scope_to_url(sel)
    return sel


def _sync_scope_to_url(sel: dict) -> None:
    """選んだ範囲を URL に残す。開き直しても戻らず、ブックマーク・共有もできる."""
    try:
        qp = st.query_params
        # 「すべて」は既定なので書かない。書くと店へのリンクにも載り、
        # 遷移先でこの同期が走って hall を消してしまう (2026-09-01)
        if sel["mode"] != "すべて":
            qp["scope"] = sel["mode"]
        elif "scope" in qp:
            del qp["scope"]
        for k in ("region", "pref", "city", "addr"):
            if sel.get(k) and sel[k] != "すべて":
                qp[k] = str(sel[k])
            elif k in qp:
                del qp[k]
        if sel.get("radius") and sel["mode"] == "近く":
            qp["radius"] = str(sel["radius"])
        elif "radius" in qp:
            del qp["radius"]
    except Exception:
        pass        # URL 同期の失敗で画面を止めない


def scope_apply(df: pd.DataFrame, sel: dict) -> pd.DataFrame:
    """scope_controls の選択を df に適用する。半径指定時は km 列を付与."""
    mode = sel.get("mode", "すべて")
    if mode == "都道府県":
        pref, reg = sel.get("pref"), sel.get("region")
        if not reg:
            reg = PREF_REGION.get(pref, "すべて")   # 古い URL は県しか持たない
        if reg == "すべて":
            return df                                  # 全国。絞らない
        if not pref or pref == "すべて":
            hsub = halls[halls["pref"].isin(REGIONS.get(reg, []))]   # 地方まるごと
        else:
            hsub = halls[halls["pref"] == pref]
        if sel.get("city") and sel["city"] != "すべて":
            hsub = hsub[city_mask(hsub["city"], sel["city"])]
        return df[df["hall"].isin(set(hsub["hall"]))]
    if mode == "近く" and sel.get("addr"):
        # 地点は scope_controls で確定済み (UI は1回だけ描く)。
        # ここで解決すると、一覧が2つある画面でキーが衝突する
        pt = sel.get("pt")
        if not pt:
            return df
        g = df.merge(halls[["hall", "lat", "lon"]], on="hall", how="left", suffixes=("", "_h"))
        lat_c = "lat_h" if "lat_h" in g.columns else "lat"
        lon_c = "lon_h" if "lon_h" in g.columns else "lon"
        g = g.dropna(subset=[lat_c, lon_c])
        if g.empty:
            st.info("座標データ整備中のため距離検索はまだ使えません")
            return df
        g["km"] = g.apply(lambda r: haversine_km(pt[0], pt[1], r[lat_c], r[lon_c]), axis=1)
        rad = sel.get("radius", 10)
        if "geo_approx" in g.columns and rad < 5:
            # 市区の中心で代用した店は誤差 1〜2km ある。半径が小さいときは
            # 混ぜると誤った結果になるので外す (2026-09-03)
            g = g[g["geo_approx"].fillna(0) == 0]
        return g[g["km"] <= rad].sort_values("km")
    return df


def scope_ui(df: pd.DataFrame, key: str = "geo") -> pd.DataFrame:
    """UI を描いてそのまま適用する (1画面で1回しか呼ばない場合の短縮形)."""
    return scope_apply(df, scope_controls("geo"))


# カード内の店名ボタンを「見出し行そのもの」に見せる (2026-09-01)。
# カードの見た目なのに店名の文字部分しか押せず、触るまで分からないという指摘への対応。
# Streamlit にコンテナ全体をクリック可能にする機能は無い。透明ボタンを重ねる手法は
# DOM 構造に依存しバージョン更新で壊れるため採らない。ここは文字の配置と色だけを
# 変えるので、崩れても表示が少し変わるだけで機能は落ちない。
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700&family=Noto+Sans+JP:wght@400;500;700&display=swap');

/* 日本語の約物が全角のままだと注記行に大きな穴が開く */
html, body { font-feature-settings: "palt" 1; }
/* 折りたたみの矢印などは Material Symbols のリガチャ。font-family を奪うと
   "keyboard_arrow_right" という英単語が表示される。必ず守る */
[data-testid="stIconMaterial"], span[class*="material"] {
  font-family: "Material Symbols Rounded", "Material Symbols Outlined" !important;
}

/* --- ファーストビューの回収 ---
   実測: 最初のカードが Y=421〜593px にあり、初画面に 1.3〜2.9件しか出ていなかった。
   内訳の大半は Streamlit のヘッダー60px と既定の上96px/下160pxパディングで、
   こちらが意図して置いた余白ではない (2026-09-01 レビュー) */
header[data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
.stMainBlockContainer { padding-top: 14px !important; padding-bottom: 56px !important; }
[data-testid="stHeaderActionElements"] { display: none !important; }
/* --- 見えない要素のギャップを消す (2026-09-04 ユーザー指摘「タイトルの上が空きすぎ」) ---
   <style> と <meta> を st.markdown で入れているが、Streamlit は要素ごとに
   縦 1rem のギャップを置くので、中身が 0px でも 2つで 32px を食っていた
   (実機で上端〜タイトルが約 50px。意図した余白は 14px)。
   要素コンテナ自体を非表示にするとギャップも消える。<style> は祖先が
   display:none でも CSS として効く。可視タグを含むものは誤爆しないよう除外 */
div[data-testid="stElementContainer"]:has(:is(style, meta)):not(:has(p, a, span, img, table, button, input, iframe, h1, h2, h3, h4, h5, h6, ul, ol, li)) {
  /* 最初は「> stMarkdownContainer」と直接の子で書いて効かなかった (実測 2026-09-04:
     style/meta の行が display:block のまま 14→30→46px と積み上がっていた)。
     入れ子を決め打ちせず、子孫にだけ効かせる */
  display: none !important;
}

/* ホールスコアの見た目 (2026-09-01)。配色の土台は .streamlit/config.toml。
   ここでは Streamlit の既定部品をカードらしく作り込む。
   方針: 白いカードをやめて地に緑を含ませ、数字だけを深緑で立たせる。 */

/* st.link_button は target="_blank" で新しいタブを開く仕様。
   店の詳細が別タブで開いてしまい、ナビの状態も引き継がれなかった
   (2026-09-01 実機で「本日を押しても戻らない」として顕在化)。
   属性は Python から変えられないので、CSS ではなく JS でもなく、
   同一タブ遷移は link_button では実現できない。→ 下の注記を参照 */

/* --- カード = ボタンそのもの ---
   Streamlit 1.56 の枠付きコンテナは stVerticalBlock という汎用要素で、専用の
   data-testid を持たない。生成クラス (st-emotion-cache-…) はバージョンで変わるため
   狙えない。コンテナに頼らず、ボタン自体をカードとして描く。
   見た目のカード = タップ範囲になり、構造も単純になる (2026-09-01)。 */
div[data-testid="stButton"], div[data-testid="stLinkButton"] { width: 100%; }

div[data-testid="stButton"] button[kind="tertiary"],
div[data-testid="stLinkButton"] a[kind="tertiary"] {
  width: 100% !important;
  display: flex !important;
  justify-content: flex-start !important;
  text-align: left !important;
  background: #ffffff !important;
  border: 1px solid #c9d6cc !important;
  border-radius: 14px !important;
  padding: 13px 15px !important;
  margin-bottom: 0;
  box-shadow: 0 1px 2px rgba(20, 40, 25, .07);
  color: #191c22 !important;
  transition: background .12s, border-color .12s;
}
div[data-testid="stButton"] button[kind="tertiary"]:hover,
div[data-testid="stLinkButton"] a[kind="tertiary"]:hover {
  background: #f4f9f5 !important;
  border-color: #9dbca7 !important;
}
div[data-testid="stButton"] button[kind="tertiary"]:active,
div[data-testid="stLinkButton"] a[kind="tertiary"]:active { background: #e9f2ec !important; }
/* ボタン内側の入れ子。既定では中身が flex で中央に寄せられ、カードごとに
   文字の開始位置がずれていた (実測: ボタン704px に対し中身267px)。
   内側を全部 100% にして左端から始める (2026-09-01) */
div[data-testid="stLinkButton"] a[kind="tertiary"] > div,
div[data-testid="stLinkButton"] a[kind="tertiary"] [data-testid="stMarkdownContainer"],
div[data-testid="stButton"] button[kind="tertiary"] > div,
div[data-testid="stButton"] button[kind="tertiary"] > div > span,
div[data-testid="stButton"] button[kind="tertiary"] [data-testid="stMarkdownContainer"] {
  width: 100% !important;
  justify-content: flex-start !important;
  text-align: left !important;
}
/* ラベルは複数行。マークダウンの改行は段落内の <br> になるため
   行ごとの指定はできない。大きさと行間だけ整える */
div[data-testid="stLinkButton"] a[kind="tertiary"] p,
div[data-testid="stButton"] button[kind="tertiary"] p {
  text-align: left !important;
  line-height: 1.55;
  font-size: 0.87rem;
  font-weight: 400;
  margin: 0;
  color: #5b6270;
}
/* 数字は色付き文字で出し、ここで大きさを与えて主役にする。
   色を問わず一括で拡大すると :red[] を使った瞬間に崩れるので色ごとに分ける */
div[data-testid="stLinkButton"] a[kind="tertiary"] p span[style*="rgb(21, 130, 55)"],
div[data-testid="stButton"] button[kind="tertiary"] p span[style*="rgb(21, 130, 55)"] {
  font-size: 1.34rem; font-weight: 700;
  font-variant-numeric: tabular-nums; letter-spacing: -0.01em;
}
div[data-testid="stLinkButton"] a[kind="tertiary"] p span[style*="rgb(255, 43, 43)"],
div[data-testid="stButton"] button[kind="tertiary"] p span[style*="rgb(255, 43, 43)"] {
  font-size: 1.34rem; font-weight: 700; font-variant-numeric: tabular-nums;
}
/* 副次情報 (信頼度など) は本文より小さく落とす */
div[data-testid="stLinkButton"] a[kind="tertiary"] p span[style*="rgb(255, 161, 33)"],
div[data-testid="stButton"] button[kind="tertiary"] p span[style*="rgb(255, 161, 33)"] {
  font-size: .78rem; color: #7c8590 !important;
}
/* 1行目の店名 */
div[data-testid="stLinkButton"] a[kind="tertiary"] p strong,
div[data-testid="stButton"] button[kind="tertiary"] p strong {
  font-size: 1.02rem; font-weight: 700; color: #191c22;
}

/* --- カード (自前の <a>) ---
   st.link_button は必ず新しいタブで開く仕様のため使えない。生の <a> にすると
   同一タブ遷移になり、スクロールも先頭に戻る。行ごとにスタイルを当てられる
   ようになったので、数字とタグを独立した要素として組める (2026-09-01) */
a.hs-card {
  display: block;
  background: #ffffff;
  border: 1px solid #c9d6cc;
  border-radius: 14px;
  padding: 13px 15px;
  margin-bottom: 9px;
  text-decoration: none !important;
  color: #191c22;
  box-shadow: 0 1px 2px rgba(20, 40, 25, .07);
  transition: background .12s, border-color .12s;
}
a.hs-card:hover { background: #f4f9f5; border-color: #9dbca7; }
a.hs-card:active { background: #e9f2ec; }
a.hs-card > span { display: block; }
a.hs-card .c-title {
  font-size: 1.02rem; font-weight: 700; color: #191c22; line-height: 1.45;
}
a.hs-card .c-title::after { content: " ›"; color: #aab6ad; font-weight: 400; }
a.hs-card .c-meta { font-size: .8rem; color: #6b7280; margin-top: 1px; }
a.hs-card .c-num {
  display: inline-block !important;
  font-size: 1.34rem; font-weight: 700; color: #14532d;
  font-variant-numeric: tabular-nums; letter-spacing: -.01em;
  margin-top: 5px;
}
a.hs-card .c-note {
  display: inline-block; font-size: .76rem; color: #7c8590; margin-left: 7px;
}
a.hs-card .c-sub { font-size: .83rem; color: #4b525c; margin-top: 3px; }
a.hs-card .c-tags { margin-top: 6px; }
a.hs-back {
  display: inline-block; font-size: .82rem; color: #14532d;
  text-decoration: none !important; padding: 7px 13px; margin-bottom: 8px;
  border: 1px solid #c9d6cc; border-radius: 999px; background: #ffffff;
}
a.hs-back:hover { background: #f4f9f5; }
a.hs-card .c-tags b {
  display: inline-block; font-weight: 400; font-size: .72rem;
  background: #e6ebe6; color: #55605a;
  border-radius: 999px; padding: 2px 9px; margin: 0 4px 3px 0;
}

/* --- ブランドバー: 見出しを1行に詰める --- */
.brandbar { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap;
            /* 上の -8px は、前にある見えない要素のギャップを相殺するための応急処置
               だった。ギャップ自体を消したので 0 に (2026-09-04) */
            margin: 0 0 10px; }
.brandbar strong { font-size: 1.05rem; letter-spacing: .01em; color: #14532d; }
.brandbar span { font-size: .74rem; color: #6b7280; }

/* --- ナビ: カードと同じ白にし、指で押しやすい大きさにする ---
   実測で判明した名前を使う。stSegmentedControl は存在せず、
   stButtonGroup / stBaseButton-segmented_control が正しい (2026-09-01) */
div[data-testid="stButtonGroup"] button {
  background: #ffffff !important;
  border: 1px solid #c9d6cc !important;
  border-radius: 10px !important;
  /* 既定で height が固定されており padding を足しても伸びなかった (実測32px) */
  height: auto !important;
  padding: 7px 11px !important;
  min-height: 34px !important;
}
div[data-testid="stButtonGroup"] button p,
div[data-testid="stButtonGroup"] button div {
  font-size: .84rem !important;
  font-weight: 500 !important;
}
/* --- チップの間隔はサイト全体で 6px に揃える (2026-09-03)。
   注意: stButtonGroup 自体は display:block で、実際にボタンを並べている
   flex コンテナは1つ内側の div (test-id なし)。外側に gap を書いても効かない */
div[data-testid="stButtonGroup"] > div {
  display: flex !important;
  flex-wrap: wrap !important;
  gap: 6px !important;
}
/* 選択中 (白ピル共通)。上の規則 (要素2+属性1) に勝つよう詳細度を上げる。
   同じ !important 同士では詳細度で決まる (2026-09-01 に踏んだ) */
div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {
  background: #14532d !important;
  border-color: #14532d !important;
}
div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] p,
div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] div {
  color: #ffffff !important;
}

/* --- 主ナビ (key=page) は下線タブ様式 (2026-09-04 ユーザー判断で「傾向」の
   タブと見た目を交換) ---
   白ピルは副ナビ (傾向/検証/このサイト) と期間選択に残し、主ナビだけを
   「透明・太字・選択中は深緑の 3px 下線」にする。ピルを2段重ねないための階層表現。

   文字は画面幅いっぱいまで大きくする (2026-09-04 ユーザー指摘「結構小さい」)。
   6項目 20文字。各ボタンは文字数に応じた幅で flex:1 1 auto、隙間ゼロで
   space-between。均等幅にはしない (「ランキング」5字が「本日」2字と同じ幅だと溢れる)。
     Android 412px: 使える幅 388 − 余白3px×12=36 → 352px / 20字 = 17.6px 上限 → 1.05rem
     iPhone  390px: 使える幅 366 − 36 → 330px / 20字 = 16.5px 上限 → 1.0rem
   デスクトップは全幅に広げない (700px に広がると間延びする)。
   キーで狙う (Streamlit は key を st-key-<key> クラスでコンテナに付ける) */
[class*="st-key-page"] div[data-testid="stButtonGroup"] > div {
  gap: 8px !important;
  border-bottom: 1px solid #c9d6cc;
  flex-wrap: nowrap !important;
}
[class*="st-key-page"] div[data-testid="stButtonGroup"] button {
  background: transparent !important;
  border: none !important;
  border-bottom: 3px solid transparent !important;
  border-radius: 6px 6px 0 0 !important;
  box-shadow: none !important;
  padding: 9px 5px !important;    /* 3px は詰まりすぎ (ユーザー指摘) → 5px */
  min-height: 42px !important;
  min-width: 0 !important;
  margin: 0 !important;
}
[class*="st-key-page"] div[data-testid="stButtonGroup"] button p,
[class*="st-key-page"] div[data-testid="stButtonGroup"] button div {
  font-size: 1.0rem !important;
  /* 未選択の色と太さ (2026-09-04、ユーザー指摘2回で中間に落ち着いた):
       #6b7280/700 → 「濃くて、下線があるからまだ分かるけど」
       #9aa3ad/500 → 「薄すぎて視認しにくい」
       #7c8590/600 ← 採用。サイト内で注記に使っている色。選択中 (#14532d/700/下線)
     との差は色・太さ・下線の3つで残す */
  font-weight: 600 !important;
  color: #7c8590 !important;
  white-space: nowrap !important;
}
[class*="st-key-page"] div[data-testid="stButtonGroup"] button:hover { background: #f4f9f5 !important; }
[class*="st-key-page"] div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] {
  background: transparent !important;
  border-bottom-color: #14532d !important;
}
[class*="st-key-page"] div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] p,
[class*="st-key-page"] div[data-testid="stButtonGroup"] button[data-testid="stBaseButton-segmented_controlActive"] div {
  color: #14532d !important;
  font-weight: 700 !important;
}
/* --- 主ナビをスクロールしても残す (2026-09-04 ユーザー指摘) ---
   スクロールコンテナは section.stMain。その中で sticky にする。
   背景を地の色にしないと、下を流れるカードが透ける。ブランドバーは固定しない */
div[data-testid="stElementContainer"][class*="st-key-page"] {
  position: sticky !important;
  top: 0 !important;
  z-index: 50 !important;
  background: #e6ebe7 !important;
  padding-top: 4px !important;
}
/* スマホ: 幅いっぱいに広げる。閾値 480px は Android 412px / iPhone 390px の両方を含む
   (400px では Android で発火しなかった、2026-09-04 実機) */
@media (max-width: 480px) {
  [class*="st-key-page"] div[data-testid="stButtonGroup"] > div {
    gap: 0 !important;
    justify-content: space-between !important;
  }
  [class*="st-key-page"] div[data-testid="stButtonGroup"] button { flex: 1 1 auto !important; }
  /* 1.05rem/余白3px は「詰まりすぎ」(ユーザー指摘)。文字を 1.0rem に戻して余白 5px に。
     412px: 16px×20字=320 + 5px×12=60 → 380px < 388px */
  [class*="st-key-page"] div[data-testid="stButtonGroup"] button p,
  [class*="st-key-page"] div[data-testid="stButtonGroup"] button div { font-size: 1.0rem !important; }
}
/* iPhone 幅 (390px、使える幅 366px): 15.2px×20字=304 + 60 → 364px */
@media (max-width: 400px) {
  [class*="st-key-page"] div[data-testid="stButtonGroup"] button p,
  [class*="st-key-page"] div[data-testid="stButtonGroup"] button div { font-size: .95rem !important; }
}
/* --- 折りたたみ: カードと同じ白。背景は details と中身の両方に当てる --- */
div[data-testid="stExpander"] details,
div[data-testid="stExpander"] summary,
div[data-testid="stExpanderDetails"] {
  background: #ffffff !important;
}
div[data-testid="stExpander"] details {
  border: 1px solid #c9d6cc !important;
  border-radius: 14px !important;
  box-shadow: 0 1px 2px rgba(20, 40, 25, .07);
  overflow: hidden;
}
div[data-testid="stExpander"] summary { padding: 11px 15px !important; }

/* --- カード内の階層 (モック C+ に寄せる) ---
   1行目=店名, 2行目=ルール, 3行目=数字, 4行目=機種。
   マークダウンの改行は段落内の <br> なので行ごとの指定はできない。
   店名は太字、数字は色付き span という「印」を手がかりに大きさを与える */
/* カード間の空きを詰める。実測26px は空きすぎで、1画面あたり0.2〜0.3件の損失。
   stVerticalBlock は汎用要素なので、カードを含むブロックだけに効くよう
   ボタンを持つ要素に限定する */
div[data-testid="stVerticalBlock"]:has(> div [data-testid="stButton"] button[kind="tertiary"]) {
  gap: 8px !important;
}

/* --- 枠付きコンテナもカードにする ---
   カードの装飾をボタンへ移した結果、st.container(border=True) を使う画面
   (傾向Q&A・店舗詳細) だけ素のまま平坦に残っていた (2026-09-01 ユーザー指摘)。
   1.56 の枠付きコンテナは汎用の stVerticalBlock で専用の testid を持たないため、
   container に key を付けて出る st-key-* クラスで狙う */
[class*="st-key-card"] {
  background: #ffffff !important;
  border: 1px solid #c9d6cc !important;
  border-radius: 14px !important;
  box-shadow: 0 1px 2px rgba(20, 40, 25, .07);
  padding: 13px 15px !important;
}
/* カードの中に置いたボタン/リンクは、カード装飾を二重に持たない。
   ここに全リンクを巻き込むと、一覧のカードまで背景と枠線が消える
   (一括置換の事故。2026-09-01) */
[class*="st-key-card"] div[data-testid="stButton"] button[kind="tertiary"],
[class*="st-key-card"] div[data-testid="stLinkButton"] a[kind="tertiary"] {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
}

/* 検索ボックスだけ素の見た目 (角0・透明) で浮いていた */
[data-baseweb="select"] > div {
  background: #ffffff !important;
  border: 1px solid #c9d6cc !important;
  border-radius: 12px !important;
  min-height: 44px !important;
}

/* ラジオのタップ標的が26px しかなかった (推奨44px)。チップ状にして押しやすくする */
[data-testid="stRadio"] [role="radiogroup"] { gap: 6px !important; flex-wrap: wrap; }
/* 選択肢だけをチップにする。[data-testid="stRadio"] label だと
   ウィジェットの見出し (「特徴で絞り込み」等) まで白い箱になっていた
   (2026-09-02 実測: 見出しに背景#fff・枠線・角丸999px が付いていた) */
[data-testid="stRadio"] [role="radiogroup"] label {
  min-height: 40px !important;
  padding: 7px 13px !important;
  border: 1px solid #c9d6cc;
  border-radius: 999px;
  background: #ffffff;
  margin: 0 !important;
  align-items: center;
}
/* 見出しと選択肢が詰まりすぎて一体に見えなかった */
[data-testid="stRadio"] [data-testid="stWidgetLabel"] { margin-bottom: 7px !important; }

/* 店舗詳細のジャグラー帯。打つかどうかの判断に直結するので前に出す */
.jug-band {
  display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px 10px;
  background: #ffffff; border: 1px solid #c9d6cc; border-left: 5px solid #9aa8a0;
  border-radius: 12px; padding: 11px 14px; margin: 2px 0 10px;
}
.jug-band .jug-cap  { font-size: .78rem; color: #6b7280; width: 100%; }
.jug-band .jug-num  { font-size: 1.9rem; font-weight: 800; line-height: 1.05; color: #191c22; }
.jug-band .jug-lab  { font-size: .95rem; font-weight: 700; padding: 2px 10px;
                      border-radius: 999px; background: #eef1ef; color: #55605a; }
.jug-band .jug-note { font-size: .78rem; color: #6b7280; margin-left: auto; }
.jug-band.jug-hot   { border-left-color: #14532d; }
.jug-band.jug-hot  .jug-num { color: #14532d; }
.jug-band.jug-hot  .jug-lab { background: #14532d; color: #ffffff; }
.jug-band.jug-warm  { border-left-color: #4a8c62; }
.jug-band.jug-warm .jug-lab { background: #dcecdf; color: #14532d; }
.jug-band.jug-cool  { border-left-color: #c98d86; }
.jug-band.jug-cool .jug-lab { background: #f6e4e2; color: #8c2f26; }
.jug-band.jug-cold  { border-left-color: #b3261e; }
.jug-band.jug-cold .jug-num { color: #b3261e; }
.jug-band.jug-cold .jug-lab { background: #b3261e; color: #ffffff; }

/* カードの数字の隣に置く判定ラベル (ジャグラーの甘辛)。
   甘い側は緑で強調し、辛い側も色を付ける。避けたい店ほど目に入るべき */
/* イベントの予想印。◎本命 は深緑の塗り、○対抗 は淡い緑。
   ジャグラーの甘辛と同じ考え方で、強いものほど濃くする */
.mk-line { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 8px;
           margin-bottom: 2px; }
.mk-line b { font-size: 1.02rem; }
.mk-line .mk-n { font-size: .78rem; color: #6b7280; }
.mk { display: inline-block; font-size: .8rem; font-weight: 700;
      padding: 2px 10px; border-radius: 999px; }
.mk-hon { background: #14532d; color: #ffffff; }
.mk-tai { background: #dcecdf; color: #14532d; }

/* データがない店の行 (2026-09-03) */
.ndrow{padding:6px 0;border-bottom:1px solid #eceeed}
.ndrow b{font-size:.9rem;font-weight:600}
.ndrow span{display:block;font-size:.76rem;color:#6b7280;margin-top:2px}

/* 店の位置づけ。次元ごとに帯で出す (2026-09-03) */
.dimrow{display:flex;align-items:center;gap:8px;margin:5px 0}
.dimname{flex:0 0 92px;font-size:.84rem;color:#4b525c}
.dimbar{flex:1;height:9px;background:#e6ebe7;border-radius:5px;overflow:hidden}
.dimbar i{display:block;height:100%;background:#9fb3a5;border-radius:5px}
.dimbar i.dim-hot{background:#14532d}
.dimbar i.dim-warm{background:#3f7d52}
.dimbar i.dim-cool{background:#c3ccc6}
.dimpct{flex:0 0 62px;text-align:right;font-size:.8rem;color:#191c22;
 font-variant-numeric:tabular-nums}

/* 週間の俯瞰。横スクロールで2週間ぶん並べる。
   日付は「同じ画面の絞り込み」であって移動ではないので、<a href> ではなく
   ボタンで出す。href だと完全な再読み込みになり、選んでいたエリアが
   消えていた (2026-09-03)。ボタン列は st.columns が作る横並びを
   そのまま横スクロールさせる */
.st-key-wkrow div[data-testid="stHorizontalBlock"]{
 flex-wrap:nowrap!important;overflow-x:auto;gap:6px!important;
 padding:4px 0 8px;-webkit-overflow-scrolling:touch}
.st-key-wkrow div[data-testid="stHorizontalBlock"] > div{
 flex:0 0 auto!important;width:auto!important;min-width:0!important}
div[class*="st-key-wk_"] button{
 margin:0!important;   /* gap の外側に乗る既定マージンを消す */
 min-width:66px;padding:7px 6px!important;border:1px solid #c9d6cc!important;
 border-radius:11px!important;background:#fff!important;color:#191c22!important;
 line-height:1.25;white-space:pre;text-align:center}
div[class*="st-key-wk_"] button p{font-size:.72rem;margin:0}
div[class*="st-key-wk_"] button:hover{border-color:#14532d!important}
/* 土日は日付を赤に、検証済が多い日は枠と背景を強調する */
div[class*="st-key-wk_"][class*="-we"] button{color:#b3261e!important}
div[class*="st-key-wk_"][class*="-hot"] button{
 border-color:#14532d!important;background:#f4f9f5!important;font-weight:700}
div[class*="st-key-wk_"][class*="-sel"] button{
 border-color:#14532d!important;background:#14532d!important;color:#fff!important}

.scope-line {
  font-size: .88rem; color: #4b525c; padding: 9px 0 4px;
}
.scope-line b { color: #14532d; font-weight: 700; }

/* ピルが2つ以上並ぶ (ジャグラー / AT機) ので flex + gap にする。
   margin-left を 0 にするだけだと2つ目もくっつく (2026-09-02) */
a.hs-card .c-tagline {
  display: flex; flex-wrap: wrap; gap: 6px; margin-top: 5px;
}
a.hs-card .c-tagline .c-tag { margin-left: 0; }
a.hs-card .c-tag {
  /* a.hs-card > span { display: block } があるので、明示しないと
     ブロックのまま横いっぱいに伸びる (2026-09-02 実機で発覚)。
     c-num / c-note が inline-block を明示しているのと同じ理由 */
  display: inline-block;
  font-size: .82rem; font-weight: 700; margin-left: 7px;
  padding: 1px 8px; border-radius: 999px;
  background: #eef1ef; color: #55605a;
  vertical-align: 2px;
}
a.hs-card .c-tag.c-hot  { background: #14532d; color: #ffffff; }
a.hs-card .c-tag.c-warm { background: #dcecdf; color: #14532d; }
a.hs-card .c-tag.c-cool { background: #f6e4e2; color: #8c2f26; }
a.hs-card .c-tag.c-cold { background: #b3261e; color: #ffffff; }

/* 注記が本文と同じ大きさ (14px) で、データと同じ重さで場所を取っていた */
[data-testid="stCaptionContainer"] {
  font-size: 12px !important; line-height: 1.7; opacity: .58 !important;
}

/* バッジ (`末尾2` 等のコード表記) を丸いピルにする */
div[data-testid="stLinkButton"] a[kind="tertiary"] code,
div[data-testid="stButton"] button[kind="tertiary"] code,
div[data-testid="stMarkdownContainer"] code {
  background: #e6ebe6;
  color: #55605a;
  border: none;
  border-radius: 999px;
  padding: 2px 9px;
  font-size: 0.72rem;
  font-family: inherit;
}
</style>""", unsafe_allow_html=True)

# 検索結果やSNSに貼ったときの見え方。theme-color は iOS Safari のアドレスバーを
# 地の色に染めるので、体感の「アプリらしさ」が1行で上がる (2026-09-01 レビュー)
st.markdown("""
<meta name="description" content="パチスロの出玉データを統計処理し、いつ・どの店に行くか、何を打つかの判断材料を出すサイト。東京・神奈川の約370店。統計的に確かめられたことと確かめられなかったことを区別して掲載しています。">
<meta name="theme-color" content="#e6ebe7">
<meta property="og:title" content="ホールスコア | スロット店舗データ">
<meta property="og:description" content="出玉データの統計。どの店のどのイベント日が効くか、その店で何を打つかを、検証済みで。">
<meta property="og:type" content="website">
<meta property="og:url" content="https://hallscore.com/">
""", unsafe_allow_html=True)

# URL から範囲指定を復元する。開き直しても「すべて」に戻らず、
# ブックマークや共有でも同じ絞り込みが再現される (2026-09-01)
for _qk, _sk in (("scope", "sc_mode_geo"), ("region", "sc_region_geo"),
                 ("pref", "sc_pref_geo"), ("city", "sc_city_geo"),
                 ("addr", "sc_addr_geo")):
    try:
        if _qk in st.query_params and _sk not in st.session_state:
            st.session_state[_sk] = st.query_params[_qk]
    except Exception:
        pass
try:
    if "radius" in st.query_params and "sc_radius_geo" not in st.session_state:
        st.session_state["sc_radius_geo"] = int(st.query_params["radius"])
except Exception:
    pass
# 店の詳細は URL で開く。リンクによる遷移なのでスクロール位置は自動的に先頭へ戻り、
# 店ごとの URL を共有・ブックマークできる (2026-09-01)
# URL に hall があれば店舗詳細。ナビを押したときに hall を消すことで、
# 「hall があれば店舗詳細」という関係が常に成立するようにする。
# session_state のフラグで初回だけ反映する方式は、セッションが再利用されると
# ウィジェットの値が優先されて効かなかった (2026-09-01 実機で判明)。
def _on_nav_change() -> None:
    try:
        if "hall" in st.query_params:
            del st.query_params["hall"]
    except Exception:
        pass
    st.session_state.hall_pick = None
    # 条件は画面をまたいで保たれるので、移動したらエリアの操作面は畳む。
    # 開いたままだと3段のチップが毎ページ先頭を占める (2026-09-02)
    for k in list(st.session_state):
        if k.startswith("scope_open_"):
            st.session_state[k] = False


try:
    if "hall" in st.query_params:
        # 初回描画のときだけ session_state に入れる。既にウィジェットがある状態で
        # 代入すると on_change が発火し、その中で hall を消してしまう
        # (自分で仕掛けた処理に自分が打ち消される。2026-09-01 実機で判明)
        if "page" not in st.session_state:
            st.session_state["page"] = "店舗"
        st.session_state.hall_pick = st.query_params["hall"]
except Exception:
    pass

# 店舗数は DB から数える。収集が進めば自動で増え、書き換えが要らない。
# 「全国全て」のような固定文言は、地域が揃うまで事実と違ううえ、
# 検証したことだけ載せるという方針そのものを疑わせる (2026-09-01)
st.markdown(f'<div class="brandbar"><strong>ホールスコア</strong>'
            f'<span>{len(halls):,}店の出玉データを解析</span></div>',
            unsafe_allow_html=True)
# ?page_date= 付きの URL で開かれたときは「イベント」を開く。
# 既定値を入れる前に判定する。後ろに置くと page が必ず存在してしまい
# 「初回だけ」が成立せず、ナビで別の画面を押した直後に戻される
# (2026-09-03 実機で発生)
try:
    if ("page_date" in st.query_params and "hall" not in st.query_params
            and "page" not in st.session_state):
        st.session_state["page"] = "イベント"
except Exception:
    pass
# ?page=本日 / ?page=傾向&sub=検証 で特定の画面を直接開く (2026-09-04)。
# X やメッセージで「今日の狙い目」「検証したこと」を直リンクで貼るため。
# page_date と同じく「初回だけ」。ナビ操作で URL には書き戻さない (履歴を汚さない)。
# PAGES に無い値 (旧ページ名など) は無視して既定画面に落ちる。
# sub は segmented_control (key=sub_page) の生成前に session_state に入れる。
# default= と併用すると Streamlit が二重指定を拒むので、その部品は default を持たない
try:
    if ("page" in st.query_params and "hall" not in st.query_params
            and "page" not in st.session_state):
        _qp_page = st.query_params["page"]
        if _qp_page in PAGES:
            st.session_state["page"] = _qp_page
            _qp_sub = st.query_params.get("sub", "")
            if _qp_page == "傾向" and _qp_sub in ("傾向", "検証", "このサイト") \
                    and "sub_page" not in st.session_state:
                st.session_state["sub_page"] = _qp_sub
except Exception:
    pass
if st.session_state.get("page") not in PAGES:  # 未設定・旧ページ名の両方をリセット
    st.session_state.page = HOME_PAGE
page = st.segmented_control("画面", PAGES, key="page", label_visibility="collapsed",
                            on_change=_on_nav_change)
if page is None:  # 選択中セグメントの再タップで解除された場合は既定画面に戻す
    page = HOME_PAGE
# URL に hall があれば必ず店舗詳細。ウィジェットの値より URL を優先する
try:
    if "hall" in st.query_params:
        page = "店舗"
except Exception:
    pass
if st.session_state.get("_last_page") != page:   # 切り替わったときだけ記録
    st.session_state["_last_page"] = page
    track("page", page)

# ---------- ⓪ 今日どこに行くか (店の一覧) ----------
# 台番の推奨を外した時点で、この画面が出しているのは店であって台ではない。
# 「狙い台」というラベルは実態と合わなくなっていた (2026-09-01 ユーザー指摘)。
# 見出しがそのまま「今日どこ行く」という問いの答えになる形にする。
# なお店舗詳細の「ジャグラーの狙い台」は実際に台番を出しているので、そちらは据え置き。

# 直前に見ていた画面を覚えておく (流入元の記録用)。
# 店舗詳細は URL 経由でも開くので、ナビの値だけでは来歴が分からない
if page != "店舗":
    st.session_state["_last_page"] = page

if page == "本日":
    today = dt.date.today().isoformat()
    td = q("""SELECT hall, pref, rules, uplift_mai, uplift_pt, p_shuffle, concentrated,
                     score, oos_state, oos_test_pt, oos_test_mai
              FROM upcoming_days WHERE date=? ORDER BY score DESC""", (today,))
    st.markdown(f"##### {fmt_date(today)} 行くならこの店")
    st.caption("**普段の日より出ている順。** 数字は、その店のイベント日が"
               "通常日を1台あたり何枚上回ったかです。")
    # 範囲指定はページに1つ。イベント店・通常日の店の両方に効かせる。
    # ランキングと同じく折りたたみに (2026-09-04)。条件は見出しに要約
    with st.expander(f"絞り込み ｜ {scope_summary('geo')}", expanded=False):
        _geo = scope_controls("geo")
        crowd_toggle()
    if td.empty:
        st.info("本日イベント日にあたる店はありません。"
                "「イベント」で今後のイベント日を確認できます。")
    else:
        _before = td
        td = scope_apply(td, _geo)
        scope_context(_before, td, "本日イベント日の")
        with st.expander("信頼度の根拠"):
            st.markdown(
                "「この店の7のつく日は、普段の日より出る」——こういう**見立て**を、"
                "まず**古いほうのデータだけ**で作ります。そのあと"
                "**新しいほうのデータで、本当に出ていたかを確かめます**。"
                "両方そろった店だけに「信頼度」の星を付けています。  \n\n"
                "過去のデータをいくら眺めても、たまたま出ていただけの店は"
                "必ず見つかります。それを本物と見分けるための手順です。  \n\n"
                "カードの **枚数は、答え合わせのほうで実際に出た数字**です。"
                "見立てを作ったときの数字ではありません。  \n\n"
                "なお**どの台番に座るか**は、同じやり方で確かめて"
                "**当てられないと分かった**ので出していません。")
        shown = 0
        for _, r in td.iterrows():
            if shown >= 10:
                break
            rules = json.loads(r["rules"]) or []
            # その店・その日のルールで最も推せる機種
            ms = q("""SELECT machine, uplift_mai, n_units, n_days FROM machine_event_score
                      WHERE hall=? AND uplift_mai IS NOT NULL AND present=1
                      ORDER BY score DESC LIMIT 5""",
                   (r["hall"],))
            if rules and not ms.empty:
                ph = ",".join("?" * len(rules))
                rm = q(f"""SELECT machine, MAX(mai) AS rule_mai, MAX(n_days) AS rd
                           FROM rule_machine_stats WHERE hall=? AND rule IN ({ph})
                           GROUP BY machine""", (r["hall"], *rules))
                if not rm.empty:
                    ms = ms.merge(rm, on="machine", how="left")
                    hit = ms["rd"].fillna(0) >= 3
                    ms.loc[hit, "uplift_mai"] = ms.loc[hit, "rule_mai"]
                    ms = ms.sort_values("uplift_mai", ascending=False)
            if ms.empty:
                continue
            top = ms.iloc[0]
            shown += 1
            # カード全体を1つのボタンにする (2026-09-01)。見出し行だけを押せる状態では
            # 「カードなのに店名しか反応しない」という体験になるため、内容をラベルに入れる。
            # Streamlit にコンテナ自体をクリック可能にする機能は無く、透明ボタンを重ねる
            # 手法は DOM 依存で壊れるので採らない。
            sub = []
            if rules:
                sub.append(short("、".join(rules), 24))
            if "km" in r.index and not pd.isna(r.get("km", float("nan"))):
                sub.append(f"{r['km']:.1f}km")
            _num, _note = "", ""
            if pd.notna(r["uplift_mai"]):
                s_, lab, _n = stars_by_oos(r.get("oos_state"), r.get("oos_test_pt"))
                mai = (r["oos_test_mai"] if pd.notna(r.get("oos_test_mai"))
                       else r["uplift_mai"])
                _num = f"{mai:+,.0f}枚/台"
                _note = f"{lab} {s_}" if s_ else lab
            st.markdown(card_html(hall_url(r["hall"]), r["hall"], meta=" ・ ".join(sub),
                                  num=_num, num_note=_note,
                                  tag=hall_tags(st.session_state.get("show_crowd_pill", False), _crowd_kind()).get(r["hall"]),
                                  sub=f"{short(top['machine'])} {int(top['n_units'])}台"),
                        unsafe_allow_html=True)
        if shown == 0:
            st.info("条件に合う店がありません。フィルタを緩めてみてください。")
        st.caption("店名タップで詳細へ。機種はこの店での出玉率順（この店でよく出ている機種）。"
                   "台番は当てられないと検証で分かったため出していません。"
                   "店舗詳細に過去の台番の記録は残してあります（▲参考・裏付けなし）。")

    # --- イベントがない日の導線 (2026-09-01) ---
    # イベント日でなくても客は並んでいる。出せるのは「構造的な差」だけで、
    # 店の地力 (前後半の相関 r=+0.80) と機種の地力 (r=+0.44) は前向き検証を通っている。
    # 曜日の癖は落ちた (-0.011pt, t=-0.27) ので使わない。
    st.divider()
    st.markdown("##### イベントがなくても出る店")
    st.caption("イベント日でない日の出玉率が高い店です。締めていない店は期間をまたいで続きます。")
    nb = q("""SELECT n.hall, n.pref, n.payout, n.n_days, n.consistent
              FROM hall_normal_stats n
              WHERE n.n_days >= 20 ORDER BY n.payout DESC""")
    if nb.empty:
        st.info("集計中です。")
    else:
        nb = scope_apply(nb, _geo)
        only_c = st.checkbox("前後半とも上位だった店だけ", value=True, key="nb_cons")
        st.caption("期間の前半と後半のどちらでも、全店の真ん中より上だった店に絞ります。")
        if only_c:
            nb = nb[nb["consistent"] == 1]
        if nb.empty:
            st.info("条件に合う店がありません。フィルタを緩めてみてください。")
        for j, (_, r) in enumerate(nb.head(10).iterrows()):
            mm = q("""SELECT machine, dev_pt, n_unitdays FROM hall_machine_normal
                      WHERE hall=? AND consistent=1 ORDER BY dev_pt DESC LIMIT 3""",
                   (r["hall"],))
            sub = [f"通常日 {int(r['n_days'])}日分"]
            if "km" in r.index and not pd.isna(r.get("km", float("nan"))):
                sub.append(f"{r['km']:.1f}km")
            _mach = ("よく出ている機種はまだ絞れていません" if mm.empty
                     else "、".join(short(m, 16) for m in mm["machine"][:2]))
            st.markdown(card_html(hall_url(r["hall"]), r["hall"], meta=" ・ ".join(sub),
                                  num=f"{r['payout']:.1f}%", num_note="出玉率",
                                  tag=hall_tags(st.session_state.get("show_crowd_pill", False), _crowd_kind()).get(r["hall"]),
                                  sub=_mach),
                        unsafe_allow_html=True)
        st.caption("出玉率100%が収支トントンの目安です。ほとんどの店は100%を下回ります"
                   "（だから長く打つほど負けます）。機種は前半・後半のどちらでも"
                   "その店の平均を上回っていたものだけを出しています。")

# 店名検索は最もよく使う操作なので、エリアより上に常設する。
# 店舗詳細と読み物ページ (傾向・検証・サイト) には出さない (2026-09-03)
if (page in ("本日", "イベント", "ランキング", "ジャグラー", "店舗")
        and not st.session_state.get("hall_pick")):
    hall_search()

# ---------- ① 狙い目カレンダー ----------

if page == "イベント":
    up = q("""SELECT date, hall, pref, rules, uplift_pt, uplift_mai,
                     p_shuffle, concentrated, score, oos_state, oos_test_pt, oos_test_mai
              FROM upcoming_days ORDER BY score DESC""")
    if up.empty:
        st.info("スコア計算中です。しばらくお待ちください。")
    else:
        # エリアと混雑の切り替えは折りたたみに (2026-09-04)。日付・本命・信頼度は
        # 主要操作なので、この先2週間の俯瞰の下にそのまま置く
        with st.expander(f"絞り込み ｜ {scope_summary('geo')}", expanded=False):
            up = scope_ui(up, "scope")
            crowd_toggle()
        # 週間の俯瞰を先に出す。「明日どこ行くか」は最も頻度の高い行動なのに、
        # 日付を1つずつ選ばないと分からなかった。データは14日ぶんある
        # (2026-09-02)
        wk = (up.groupby("date")
                .agg(n=("hall", "size"),
                     best=("oos_test_mai", "max"),
                     ver=("oos_state", lambda x: (x == "both").sum()))
                .reset_index().sort_values("date").head(14))
        if len(wk) > 1:
            st.markdown("###### この先2週間")
            _cur = st.session_state.get("cal_date")
            # コンテナに key を付けて、CSS を :has() 無しで届かせる
            cols = st.container(key="wkrow").columns(len(wk))
            for col, (_, r) in zip(cols, wk.iterrows()):
                d = pd.to_datetime(r["date"])
                # key に状態を混ぜて CSS で色を分ける。ボタンは中身に HTML を
                # 置けないので、見た目の分岐はキー名に寄せる
                sfx = ("-we" if d.weekday() >= 5 else "") \
                    + ("-hot" if r["ver"] >= 5 else "") \
                    + ("-sel" if r["date"] == _cur else "")
                label = (f"{d.month}/{d.day} {WD_JA[d.weekday()]}\n{int(r['n'])}店"
                         + (f"\n検証済{int(r['ver'])}" if r["ver"] else ""))
                if col.button(label, key=f"wk_{r['date']}{sfx}"):
                    st.session_state["cal_date"] = r["date"]
                    st.rerun()
            st.caption("店数はイベント日の店。「検証済」は"
                       "古いデータで見えた効きが新しいデータでも出た店の数です。")

        dates = ["すべて"] + sorted(up["date"].unique())
        # 週間表から選ばれた日付があれば初期値にする
        try:
            _pd = st.query_params.get("page_date")
            if _pd in dates and "cal_date" not in st.session_state:
                st.session_state["cal_date"] = _pd
        except Exception:
            pass
        dsel = st.selectbox("日付", dates, key="cal_date",
                            format_func=lambda x: x if x == "すべて" else fmt_date(x))
        if dsel != "すべて":
            up = up[up["date"] == dsel]
        if st.checkbox("◎本命機種のある店のみ", key="hon_cal"):
            up = up[up["hall"].isin(honmei_halls())]
        st.caption("「◎本命機種」= +300枚/台クラスの機種。")
        # 信頼度は前向き検証ベース (2026-09-01)。旧シャッフルp値のフィルタは廃止
        vsel = st.radio("信頼度で絞り込み",
                        ["すべて", "検証済みのみ", "検証済み ★4以上"],
                        horizontal=True, key="oos_cal")
        st.caption("「検証済み」= 前半のデータで見えた効きが、後半のデータでも出た店。"
                   "★の大きさは後半で実際に出た枚数です。")
        if vsel != "すべて" and not up.empty:
            up = up[up["oos_state"] == "both"]
            if vsel == "検証済み ★4以上" and not up.empty:
                up = up[up["oos_test_pt"].fillna(0) >= 0.75]
        for j, (_, r) in enumerate(up.head(20).iterrows()):
            rules = "、".join(json.loads(r["rules"])) if r["rules"] else ""
            sub = []
            if rules:
                sub.append(f"🗓 {rules}")
            if r["uplift_mai"] is not None and not pd.isna(r["uplift_mai"]):
                # ★は前向き検証 (前半で見えた効きが後半にも出たか) ベース。
                # 検証済みなら「後半で実際に出た uplift」を出す (見かけの実績ではなく)
                s, lab, _n = stars_by_oos(r.get("oos_state"), r.get("oos_test_pt"))
                mai = (r["oos_test_mai"] if pd.notna(r.get("oos_test_mai"))
                       else r["uplift_mai"])
                sub.append((f"📈 通常日より約{mai:+,.0f}枚/台"
                            + (f" ・ {lab} {s}" if s else f" ・ {lab}")))
            elif rules:
                sub.append("📈 実績集計中")
            if "km" in r.index and not pd.isna(r.get("km", float("nan"))):
                sub.append(f"📍 {r['km']:.1f}km")
            _bd = hall_badges().get(r["hall"], [])
            st.markdown(card_html(hall_url(r["hall"]),
                                  f"{fmt_date(r['date'])} {r['hall']}",
                                  meta=" ・ ".join(sub), badges=_bd,
                                  tag=hall_tags(st.session_state.get("show_crowd_pill", False), _crowd_kind()).get(r["hall"])),
                        unsafe_allow_html=True)
        if len(up) > 20:
            st.caption(f"他 {len(up) - 20} 件 (フィルタで絞り込めます)")
        st.caption("店名タップで詳細へ。📈の★=この数字がどれだけ信じられるか。"
                   "枚数は1台あたり、普段の日より何枚多いかの目安。")

# ---------- ② ジャグラーランキング ----------

elif page == "ランキング":
    st.markdown("##### ランキング")
    _CAP = {"出玉 (通常日)": "数字は1台あたりの1日平均差枚。通常日＝イベント日でない普段の出方。",
            "出玉 (イベント日)": "数字はイベント日の1台あたり1日平均差枚。",
            "ジャグラーが甘い順": "数字はBIG/REGと差枚から推定した設定の平均。"
                              "全店が3.0〜3.7に収まるので、差は小さく見えます。",
            "AT機が甘い順": "数字はジャグラー以外の通常日の出玉率。"
                          "100%が収支トントンの目安です。"}
    # ジャグラーは独立したページへ移した (2026-09-02)。設定判別ができる唯一の
    # 機種群で、この店で打つかの判断が他の指標と別なので、並べ方の1つに
    # 埋めるより入口を分けたほうが辿り着ける
    METRICS = ["出玉 (通常日)", "出玉 (イベント日)", "ジャグラーが甘い順",
               "AT機が甘い順"]
    # 絞り込みを1つの折りたたみに集める (2026-09-04 レイアウト見直し)。
    # スマホ幅 (390px) の実測で、並べ方4択(2行)・エリア・混雑・信頼度4択(2行) と
    # 各注記が積み上がり、最初のカードが Y=834px = 初画面に1枚も入っていなかった。
    # 09-01 の「ファーストビュー回収」はデスクトップ幅の測定で、スマホでは崩れていた。
    # 選択中の条件は折りたたみの見出しに要約して出し、閉じていても分かるようにする。
    _lab_metric = st.session_state.get("rank_metric", METRICS[0])
    _lab_stars = st.session_state.get("ms_po", "すべて")
    _lab = (f"絞り込み ｜ {_lab_metric} ・ {scope_summary('geo')}"
            + (f" ・ 信頼度{_lab_stars}" if _lab_stars != "すべて" else ""))
    with st.expander(_lab, expanded=False):
        metric = st.radio("並べ方", METRICS, horizontal=True, key="rank_metric")
        st.caption(_CAP.get(metric, ""))
        if metric == "ジャグラーが甘い順":
            po = q("""SELECT j.hall, h.pref, h.city, j.n_unit_days AS n_days,
                             j.mean_setting AS val
                      FROM hall_juggler_stats j LEFT JOIN halls h ON h.hall=j.hall
                      WHERE j.n_unit_days >= 100 AND j.mean_setting IS NOT NULL""")
        elif metric == "AT機が甘い順":
            po = q("""SELECT a.hall, h.pref, h.city, a.n_unitdays AS n_days,
                             a.payout AS val
                      FROM hall_at_stats a LEFT JOIN halls h ON h.hall=a.hall
                      WHERE a.n_unitdays >= 500 AND a.payout IS NOT NULL""")
        else:
            kind_key = "normal" if metric == "出玉 (通常日)" else "event"
            po = q("""SELECT p.hall, h.pref, h.city, p.n_days, p.mai AS val
                      FROM hall_payout_stats p LEFT JOIN halls h ON h.hall=p.hall
                      -- 下限5日。20日にすると通常日データ (全体で33日ぶん) では
                      -- 43店しか残らない。実測では4日でも上位1割と下位1割で
                      -- 148.7枚/台日 の差が再現し、20日まで上げても220枚にしか
                      -- ならない。店数 1,523→43 の代償に見合わない (2026-09-03)
                      WHERE p.kind=? AND p.n_days >= 5""", (kind_key,))
        _before_po = po
        po = scope_ui(po, "scope")
        crowd_toggle()
        # 信頼度の目盛りは指標で違う。出玉は「日数」、ジャグラー/AT は「台日数」
        _scale = ([(5, 1.0), (10, 2.0), (20, 3.0), (40, 4.0), (80, 5.0)]
                  if metric.startswith("出玉")
                  else [(100, 1.0), (300, 2.0), (800, 3.0), (2000, 4.0), (5000, 5.0)])
        po["conf"] = po["n_days"].map(lambda n: _interp(n, _scale))
        min_s = min_stars_ui("po")
        if min_s > 0:
            po = po[po["conf"] >= min_s]
    scope_context(_before_po, po, f"{metric}のデータがある")
    # 機種の絞り込みは並べ方に統合した。絞ったまま別の指標で並ぶと
    # 「ジャグラーが甘い店」なのに -158枚/日 が上位に来る (2026-09-03)
    po = po.sort_values("val", ascending=False)
    for i, (_, r) in enumerate(po.head(20).iterrows(), 1):
        loc = (r["city"] if isinstance(r["city"], str) else "") + (
            f" ・ {r['km']:.1f}km"
            if "km" in r.index and not pd.isna(r.get("km", float("nan"))) else "")
        s = fmt_stars(r["conf"])
        _bd = hall_badges().get(r["hall"], [])
        if metric == "ジャグラーが甘い順":
            _num = f"設定 {r['val']:.2f}"
        elif metric == "AT機が甘い順":
            _num = f"{r['val']:.1f}%"
        else:
            _num = f"{r['val']:+,.0f}枚/日"
        st.markdown(card_html(hall_url(r["hall"]), r["hall"], meta=loc,
                              num=_num,
                              num_note=f"信頼度 {s}", badges=_bd,
                              tag=hall_tags(st.session_state.get("show_crowd_pill", False), _crowd_kind()).get(r["hall"])),
                    unsafe_allow_html=True)
    if po.empty:
        st.info("この条件のデータがまだありません (収集進行中の地域があります)")
    elif len(po) < 60:
        # 2026-09-02: 「普段の出方」を daily だけで測るように変えた。
        # 対照日 (イベント日に合わせて選んだ日) を混ぜると、店どうしの
        # 比較が「どちらのデータを持つか」の比較になっていたため。
        # 全日収集が終わるまで対象店が少ない状態が続く
        st.warning(f"いま比較できるのは {len(po)} 店だけです。"
                   "普段の日のデータを全店ぶん集めている最中で、"
                   "揃うまでは対象が限られます。")
    st.caption("店名タップで詳細へ。実測の1台あたり平均差枚。通常日=普段の甘さ、"
               "イベント日=イベント時の出し方 (収集進行中の地域は順次反映)。★=集計日数。")

# ---------- ②-2 ジャグラー ----------
# 設定判別ができる唯一の機種群。この店で打つかの判断が他の指標と別なので、
# ランキングの「並べ方」の1つではなく独立した入口にした (2026-09-02)

elif page == "ジャグラー":
    st.markdown("##### ジャグラーが甘い店")
    st.caption("BIG/REG の出方から1台ずつ設定を推定し、店ごとに平均したものです。"
               "AT機は出玉から設定を読めませんが、ジャグラーは読めます。")
    # 台日数の下限300。誤差が実測できるので、それで決める (2026-09-02):
    #   1台日あたりの推定設定のばらつきは SD=0.595 → SE = 0.595/√n
    #   店ごとの「本物の」ばらつきは sd=0.068 (前半後半の分解から)
    #   n=64 だと SE=0.074 で誤差のほうが大きく、順位はノイズになる
    #   n=300 で SE=0.034 = 本物のばらつきの半分。ここを下限にする
    # 下限なしだと上位20の台日数が中央値64 (全店は422) で、
    # 推定値も 3.98 など分布の外に飛んでいた
    jg = q("""SELECT j.hall, h.pref, h.city, j.n_unit_days, j.mean_setting,
                     j.drift
              FROM hall_juggler_stats j LEFT JOIN halls h ON h.hall=j.hall
              WHERE j.n_unit_days >= 100
              ORDER BY j.mean_setting DESC""")
    # ランキングと同じく折りたたみに (2026-09-04)。条件は見出しに要約
    _jg_stars = st.session_state.get("ms_jug", "すべて")
    with st.expander(f"絞り込み ｜ {scope_summary('geo')}"
                     + (f" ・ 信頼度{_jg_stars}" if _jg_stars != "すべて" else ""),
                     expanded=False):
        jg = scope_ui(jg, "scope")
        min_s = min_stars_ui("jug")
    if min_s > 0 and not jg.empty:
        conf = jg.apply(lambda r: conf_by_n(r["n_unit_days"], r["drift"])[0], axis=1)
        jg = jg[conf >= min_s]
    _jn = jug_rank()[1]
    for i, (_, r) in enumerate(jg.head(20).iterrows(), 1):
        loc = (r["city"] if isinstance(r["city"], str) else "") + (
            f" ・ {r['km']:.1f}km"
            if "km" in r.index and not pd.isna(r.get("km", float("nan"))) else "")
        s, note = stars_by_n(r["n_unit_days"], r["drift"])
        _bd = hall_badges().get(r["hall"], [])
        st.markdown(card_html(hall_url(r["hall"]), r["hall"],
                              meta=f"全{_jn}店中 上位{_jr}% {loc}".strip()
                                   if (_jr := jug_rank()[0].get(r["hall"])) else loc,
                              num=f"設定 {r['mean_setting']:.2f}",
                              tag=(_jlab := juggler_label(r["mean_setting"]),
                                   JUG_TONE.get(_jlab, "")),
                              num_note=f"信頼度 {s}",
                              sub=note or "", badges=_bd),
                    unsafe_allow_html=True)
    st.caption("店名タップで詳細へ。★=信頼度（データの多さと、時期をまたいで続いているか）。"
               "直近だけ好調/不調の店は1段下がります。各メーカー公表の設定別機械割に"
               "もとづく推定で、店間比較用。  \n"
               "通常日の実際の差枚は、**激甘の店で +4枚/日 (ほぼトントン)**、"
               "ふつうで -90枚/日、**激辛で -186枚/日**。どこに行っても平均すれば"
               "負けますが、店選びで1日あたり190枚ぶん変わります。")

# ---------- ③ 店舗情報 ----------
# 以前は else: だったため「それ以外すべて」を飲み込み、この分岐より後ろのページ
# (傾向Q&A・検証したこと・このサイトについて) が一度も表示されていなかった。
# 店舗情報の内部に st.stop() があるため、そこでスクリプトが止まる (2026-09-01 修正)
elif page == "店舗":
    hsel = st.session_state.get("hall_pick")
    if hsel:
        track_once("hall", str(hsel))
        # どこから来たかを記録する。店に辿り着いた事実だけでは、
        # 本日ページ経由なのか検索から直接なのか区別できない。
        # SEO の成果を測るにはこれが要る (2026-09-02)
        try:
            _ref = st.session_state.get("_last_page") or "直接"
            track_once("hall_from", f"{_ref}→{hsel}"[:120])
        except Exception:
            pass
    if hsel not in set(halls["hall"]):
        hsel = None
        st.session_state.hall_pick = None
    if not hsel:
        st.markdown("##### 店舗を探す")
        st.caption("以下は、選んだエリアのイベント実績の高い順。"
                   "数字はイベント日の1台あたり最大差枚。")
        # 地域フィルタは全ページ共有 (scope_ui)。一覧・検索ともイベント実績のランキング順
        base = scope_ui(halls[["hall", "pref", "city"]].copy(), "scope")
        all_options = set(base["hall"].unique())
        if not all_options:
            st.info("この条件の店がありません")
            st.stop()
        ranked = q("""SELECT h.hall, h.city,
                        (SELECT MAX(s.uplift_mai) FROM hall_rule_stats s
                          WHERE s.hall=h.hall AND s.uplift_mai>0) AS best_mai,
                        (SELECT j.mean_setting FROM hall_juggler_stats j
                          WHERE j.hall=h.hall) AS jug
                      FROM halls h""")
        ranked = ranked[ranked["hall"].isin(all_options)]
        # 絞り込みは1つの枠にまとめる。以前は見出しと選択肢が地の上に
        # ばらばらに置かれ、どこまでが操作する場所か分からなかった
        # 枠囲み → 折りたたみ (2026-09-04 レイアウト見直し)。スマホ実機で本命チェック・
        # 特徴8択 (3行)・注記が積み上がり、カードが初画面に入っていなかった。
        # ランキングと同じく、選択中の条件を見出しに要約して既定は閉じる
        _FEAT_LABEL = {"末尾": "末尾が出ていた店", "角": "端が出ていた店",
                       "連続投入": "隣同士で出ていた店"}
        _hd_feat = st.session_state.get("hd_feat", "すべて")
        _hd_lab = "絞り込み ｜ " + _FEAT_LABEL.get(_hd_feat, _hd_feat) + (
            " ・ ◎本命のみ" if st.session_state.get("hon_hd") else "")
        with st.expander(_hd_lab, expanded=False):
            if st.checkbox("◎本命機種のある店のみ", key="hon_hd"):
                ranked = ranked[ranked["hall"].isin(honmei_halls())]
            st.caption("「◎本命機種」= +300枚/台クラスの機種。")
            # 末尾・角・連続投入は表示だけ過去形にする (値は内部キーのまま)。
            # バッジと同じ判断: 固める店は見つからなかったが、数字は事実として残す
            feat = st.radio("特徴で絞り込み",
                            ["すべて", "末尾", "角", "連続投入", "曜日", "ジャグラー", "AT機", "新台"],
                            horizontal=True, key="hd_feat",
                            format_func=lambda v: _FEAT_LABEL.get(v, v))
            st.caption("店の設定配分の癖 (偏りがはっきりした店) で絞ります。"
                       "末尾・端・隣は過去の記録で、次を予測する根拠ではありません。")
        if feat != "すべて":
            # 内部キー (hall_traits の name) で照合する (2026-09-04)。
            # 以前は hall_badges の表示文字列を startswith で見ていたが、バッジを
            # 「端が出ていた」「隣同士で出ていた」に変えた (PR #8) 時点で「角」「連続投入」が
            # 何にもマッチしなくなり 0件になっていた (回帰)。表示を変えても壊れない
            # よう、判定は表示でなくキーで行う。バッジと同じ「高」のみ
            _key = {"角": "角(並びの端)"}.get(feat, feat)
            def _match(name: str) -> bool:
                return name.endswith("曜") if feat == "曜日" else name.startswith(_key)
            hit = {h for h, tr in hall_traits().items()
                   if any(lv == "高" and _match(n) for n, (lv, _d) in tr.items())}
            ranked = ranked[ranked["hall"].isin(hit)]
        # イベント実績が未収集の店 (収集進行中の地域) はジャグラー予想設定を第2キーに
        ranked = ranked.sort_values(["best_mai", "jug"], ascending=[False, False],
                                    na_position="last")
        ranked = ranked.head(30)
        st.caption("イベント実績の高い順。タップで詳細へ。")
        for i, (_, r) in enumerate(ranked.iterrows(), 1):
            info = []
            _jl2 = _jv2 = ""   # 判定の無い店に前の行の値が残らないように
            if not pd.isna(r["best_mai"]):
                info.append(f":green[+{r['best_mai']:,.0f}枚]/台")
            if not pd.isna(r["jug"]):
                # ジャグラーの情報はピル1つにまとめる。上の行に数値、下に
                # 色付きのラベルと分かれていると、赤い「激辛」が単独行に
                # 見えて店全体の評価に読める (2026-09-02 実機で指摘)。
                # 小数2桁なのは、台日数300での推定誤差 SE=0.034 に対して
                # 2桁目が ±3 = 最後の桁が不確かという妥当な粒度だから
                _jl2 = juggler_label(r["jug"])
                _jv2 = f"{r['jug']:.2f}"
            if isinstance(r["city"], str) and r["city"]:
                info.append(r["city"])
            _bd = hall_badges().get(r["hall"], [])
            # ジャグラーと AT機 は別々に出す。2026-09-04 に測り直したところ
            # 相関は r=-0.05 で、同期間でも前半→後半でもゼロだった。
            # 一方それぞれの自己相関は 0.91 / 0.75 と高い。店は両者を完全に
            # 別管理している。まとめて1つの点にしてはいけない (Issue #3 項目9)
            _al, _at, _av = at_label(r["hall"])
            _tags = []
            if _jl2 and _jl2 != "ふつう":
                _tags.append((f"ジャグラー {_jl2} {_jv2}", JUG_TONE[_jl2]))
            if _al and _al != "ふつう":
                _tags.append((f"AT機 {_al} {_av:.1f}%", _at))
            # 他の一覧と同じ「1ボタン = 1カード」に統一する。
            # 以前はコンテナ内に店名ボタンを置いていたため、カード装飾の CSS が
            # 店名ボタンに当たり、幅98〜240px のバラバラな白いピルが縦に並んでいた
            # (2026-09-01 レビューで発覚)。実装を1種類に減らして調整コストも下げる。
            st.markdown(card_html(hall_url(r["hall"]), r["hall"],
                                  meta=" ・ ".join(info), badges=_bd,
                                  # 「ふつう」は半数の店に付き、単独行を占める割に
                                  # 何も言っていない。甘辛が出た店だけ表示する。
                                  # 主語を入れるのは、赤い「激辛」が単独行にあると
                                  # 店全体の評価に読めてしまうため (2026-09-02 実機で指摘)
                                  num="", tag=_tags),
                        unsafe_allow_html=True)
        if len(ranked_all := all_options) > 30:
            st.caption(f"他 {len(ranked_all) - 30} 店 "
                       "(上の「店名で探す」かエリアで絞り込めます)")
        st.stop()
    _back = {}
    for _k in ("scope", "pref", "city", "addr", "radius"):
        if _k in st.query_params:
            _back[_k] = st.query_params[_k]
    st.markdown(
        f'<a class="hs-back" target="_self" '
        f'href="{"?" + urllib.parse.urlencode(_back) if _back else "?"}">'
        "← 店一覧に戻る</a>", unsafe_allow_html=True)
    hinfo = halls[halls["hall"] == hsel].iloc[0]
    st.markdown(f"#### {hsel}")
    _bd = hall_badges().get(hsel, [])
    if _bd:
        st.markdown(" ".join(f"`{b}`" for b in _bd))
    _tr = hall_traits().get(hsel, {})

    def _trait_detail(name: str):
        """特徴ごとの詳しい数字。行の直下に開くため関数に切り出した (2026-09-02)."""
        if name.startswith("末尾"):
            return q("""SELECT tail AS 末尾, ev_mai AS "イベント日 枚/台",
                               ct_mai AS "通常日 枚/台"
                        FROM hall_tail_stats WHERE hall=? ORDER BY tail""", (hsel,))
        if name.endswith("曜"):
            d = q("""SELECT weekday AS 曜日番号, mai AS "枚/台", n AS 台日数
                     FROM hall_weekday_stats WHERE hall=? ORDER BY weekday""", (hsel,))
            if not d.empty:
                d.insert(0, "曜日", d["曜日番号"].map(lambda w: WD_JA[int(w)]))
                d = d.drop(columns=["曜日番号"])
            return d
        if name == "角(並びの端)":
            d = q("""SELECT kind AS 日, pos AS 端からの距離, mai AS "枚/台", n AS 台日数
                     FROM hall_edge_pos WHERE hall=? ORDER BY kind, pos""", (hsel,))
            if not d.empty:
                d["日"] = d["日"].map({"event": "イベント日", "control": "通常日"}).fillna(d["日"])
            return d
        if name == "連続投入":
            d = q("""SELECT kind AS 日, p_cond AS "隣も高設定の率",
                            p_base AS 店全体の高設定率, n_total AS 台数
                     FROM hall_neighbor_stats WHERE hall=?""", (hsel,))
            if not d.empty:
                d["日"] = d["日"].map({"event": "イベント日", "control": "通常日"}).fillna(d["日"])
            return d
        if name in ("新台", "準新台", "定番機種"):
            d = q("""SELECT bucket AS 区分, mai AS "枚/台", n AS 台日数
                     FROM hall_age_stats WHERE hall=?""", (hsel,))
            if not d.empty:
                d["区分"] = d["区分"].map({"new": "新台(〜14日)", "semi": "準新台(15〜60日)",
                                          "mature": "定番(120日〜)"}).fillna(d["区分"])
            return d
        return q("""SELECT genre AS ジャンル, mai AS "枚/台", n AS 台日数
                    FROM hall_genre_stats WHERE hall=? ORDER BY mai DESC""", (hsel,))

    if _tr:
        with st.expander(f"この店の特徴 まとめ ({len(_tr)}件)", expanded=bool(_bd)):
            st.caption("末尾・端・隣同士は過去の差枚の記録です。店ごとに検定しましたが、"
                       "特定の位置に高設定を固めている店は見つかっておらず、"
                       "次を予測する根拠にはなりません（傾向Q&A参照）。")
            st.caption("「高」は一覧にバッジとして出る強い特徴、「中」はやや傾向あり。"
                       "ここに無い項目はこの店では特徴が弱いということです。"
                       "各項目を開くと数字が出ます。")
            # 各項目を expander にする。台番の記録と同じ操作感になり、
            # クリックで再実行が走らないので画面がちらつかない。
            # (ボタン + st.rerun() で作っていたが、押すたびにページ全体の
            #  クエリが走り直していた。Streamlit 1.56 は expander の入れ子を
            #  許すので、そもそもボタンで代用する必要が無かった。2026-09-02)
            for name, (lv, desc) in sorted(
                    _tr.items(), key=lambda x: (x[1][0] != "高", x[0])):
                with st.expander(f"{trait_label(name)} ({lv}) — {desc}"):
                    d = _trait_detail(name)
                    if d is not None and not d.empty:
                        st.dataframe(d, width="stretch", hide_index=True)
                    else:
                        st.caption("この項目の詳しい数字はまだありません")
    else:
        st.caption("この店では目立った特徴 (末尾・角・曜日・機種の傾向) は検出されていません。")
    _sty = q("""SELECT concentration, recent_mai, past_mai, trend
                FROM hall_style_stats WHERE hall=?""", (hsel,))
    if not _sty.empty:
        s0 = _sty.iloc[0]
        parts = []
        c = s0["concentration"]
        if pd.notna(c):
            style = ("メリハリ型 (一部の台に集中)" if c >= 0.70 else
                     "均等型 (広く散らす)" if c <= 0.58 else "標準的な配分")
            parts.append(f"⚖️ {style} (上位1割の台が浮きの{c:.0%})")
        if pd.notna(s0["trend"]):
            t = s0["trend"]
            tr = ("最近少し出る" if t >= 60 else
                  "最近少し出ない" if t <= -60 else "出方は変わらない")
            parts.append(f"📈 {tr} (直近60日 {s0['recent_mai']:+,.0f}枚 / 以前 {s0['past_mai']:+,.0f}枚)")
        if parts:
            st.caption(" ・ ".join(parts))
    dim_panel(hsel)
    jrow = q("""SELECT mean_setting, n_unit_days, drift
                FROM hall_juggler_stats WHERE hall=?""", (hsel,))
    if not jrow.empty:
        # ジャグラーはこの店で打つかの判断に直結する数字なので、
        # 他の指標と同じ大きさの metric に埋めず、帯として前に出す (2026-09-02)
        s, _ = stars_by_n(jrow["n_unit_days"].iloc[0], jrow["drift"].iloc[0])
        _jv = jrow["mean_setting"].iloc[0]
        _jl3 = juggler_label(_jv)
        _cls = {"激甘": "hot", "甘め": "warm", "辛め": "cool", "激辛": "cold"}.get(_jl3, "")
        # 全店が 3.08〜3.66 (SD 0.080) に収まるので、数字だけでは高いか
        # 低いか判断できない。順位を併記する (2026-09-03 ユーザー指摘)
        _r, _rn = jug_rank()[0].get(hsel), jug_rank()[1]
        _jrk = f"全{_rn}店中 上位{_r}% ・ " if _r else ""
        st.markdown(
            f'<div class="jug-band{" jug-" + _cls if _cls else ""}">'
            f'<span class="jug-cap">ジャグラー 平均予想設定</span>'
            f'<span class="jug-num">{_jv:.2f}</span>'
            f'<span class="jug-lab">{_jl3}</span>'
            f'<span class="jug-note">{_jrk}信頼度 {s}</span></div>',
            unsafe_allow_html=True)
    # AT機の帯。ジャグラーと並べて、方針が分かれている店が見えるようにする
    _al, _at, _av = at_label(hsel)
    if _al:
        st.markdown(
            f'<div class="jug-band{" jug-" + _at if _at else ""}">'
            f'<span class="jug-cap">ジャグラー以外 (AT機ほか) 通常日の出玉率</span>'
            f'<span class="jug-num">{_av:.1f}%</span>'
            f'<span class="jug-lab">{_al}</span>'
            f'<span class="jug-note">全店の平均 98.9%</span></div>',
            unsafe_allow_html=True)
    # イベント日の混み具合。良い店でも抽選に外れれば座れない
    _cl, _cv, _ci2 = crowd_label(hsel)
    if _cl:
        _cc = {"座れない": "cold", "席取りはかなり困難": "cold",
               "席は取りにくい": "cool", "そこそこ座れる": "warm",
               "空いていて座れる": "hot"}.get(_cl, "")
        st.markdown(
            f'<div class="jug-band{" jug-" + _cc if _cc else ""}">'
            f'<span class="jug-cap">イベント日に座れるか</span>'
            f'<span class="jug-num">{_ci2:.0%}</span>'
            f'<span class="jug-lab">{_cl}</span>'
            f'<span class="jug-note">空いている台の割合・通常日の'
            f'{_cv:.2f}倍回っている</span></div>',
            unsafe_allow_html=True)

    # 稼働の帯。良い店でも満台なら打てないので、甘辛と並べて出す。
    # ここだけ点数を出す。測り直した信頼性が 0.949 で誤差 ±6点しかなく、
    # 「72点」と「60点」に意味がある唯一の次元 (2026-09-04, Issue #3 項目5)
    _ul, _ug, _ui, _us = util_label(hsel)
    if _ul:
        _uc = {"かなり混む": "cold", "混む": "cool",
               "空いてる": "warm", "かなり空いてる": "hot"}.get(_ul, "")
        st.markdown(
            f'<div class="jug-band{" jug-" + _uc if _uc else ""}">'
            f'<span class="jug-cap">打てる余地 (通常日)</span>'
            f'<span class="jug-num">{_us}点</span>'
            f'<span class="jug-lab">{_ul}</span>'
            f'<span class="jug-note">1台あたり {_ug:,.0f}G・'
            f'ほぼ回っていない台 {_ui:.0%}<br>'
            f'空いている店ほど高い点。全店の中での位置で、誤差は±6点</span></div>',
            unsafe_allow_html=True)

    # 県の甘辛 (2026-09-04, Issue #3 項目2)。
    # 店数20以上の県だけ。県内の店のばらつきのほうが県間の差より大きいので、
    # 「甘い県だから」で店選びを代替させない書き方にする
    _pf = hinfo["pref"] if isinstance(hinfo.get("pref"), str) else None
    if _pf:
        _pr = q("SELECT score, n_halls, hall_sd, rank FROM pref_stats WHERE pref=?",
                (_pf,)) if _table_exists("pref_stats") else None
        if _pr is not None and not _pr.empty:
            _sc, _nh, _sd, _rk = (_pr["score"].iloc[0], int(_pr["n_halls"].iloc[0]),
                                  _pr["hall_sd"].iloc[0], int(_pr["rank"].iloc[0]))
            _n_pref = q("SELECT COUNT(*) c FROM pref_stats")["c"].iloc[0]
            st.caption(
                f"📍 {_pf}は全国{_n_pref}県中{_rk}位（{_sc:+.2f}ポイント・{_nh}店で集計）。"
                f"ただし{_pf}の中の店のばらつきは {_sd:.2f}ポイントで、"
                "県の差より大きいです。県ではなく店で選んでください。")
    # 交換率は基本情報の1行に入れる (2026-09-04 レイアウト見直し)。
    # st.metric のまま置くと中央寄せの大きな数字が4枚の帯の直後に浮き、
    # 様式が1つだけ違って見えていた。ジャグラーが無い店の「—」も落とす
    # (情報量ゼロの行を並べない)
    loc_parts = []
    if pd.notna(hinfo["slot_exchange_mai"]):
        loc_parts.append(f"交換率 {hinfo['slot_exchange_mai']}枚")
    loc_parts += [x for x in (hinfo["pref"], hinfo["city"]) if isinstance(x, str) and x]
    if pd.notna(hinfo["n_slot"]):
        loc_parts.append(f"スロ{int(hinfo['n_slot'])}台")
    if loc_parts:
        st.caption(" ・ ".join(loc_parts))
    ent = entry_line(hinfo)
    if ent:
        # 朝から並ぶかどうかは行く前に決めることなので、店の基本情報として前に出す。
        # 値が無い店は行ごと出さない (「—」を並べても情報量はゼロ)
        st.caption(f"🚪 {ent}")

    # --- 🔮 イベント予想 (今後14日のイベント日から選択、デフォルトは直近) ---
    st.markdown("##### 🔮 イベント予想")
    nxt = q("""SELECT date, rules FROM upcoming_days WHERE hall=? ORDER BY date""", (hsel,))
    if not nxt.empty:
        def _ev_label(i: int) -> str:
            row = nxt.iloc[i]
            rules_s = "、".join(json.loads(row["rules"]) or [])
            days_to = (dt.date.fromisoformat(row["date"]) - dt.date.today()).days
            return f"{fmt_date(row['date'])} {rules_s}".strip() + f" (あと{days_to}日)"

        _idx = st.selectbox("イベント日を選択", range(len(nxt)), format_func=_ev_label,
                            key=f"evd_{hsel}")
        sel_rules = json.loads(nxt.iloc[_idx or 0]["rules"]) or []
    else:
        sel_rules = []
        st.info("今後14日にイベント日がありません")

    # present=1 = 店の最新データから60日以内に目撃。撤去済みを勧めない (2026-09-02)
    ms = q("""SELECT machine, n_units, n_days, uplift_mai, payout_recent, last_seen,
                     machine_first_seen, machine_censored, machine_avg_games, score
              FROM machine_event_score WHERE hall=? AND present=1
              ORDER BY score DESC""", (hsel,))
    # 選んだイベント日のルール専用の実績があれば、そのルールでの成績に差し替える
    # (実データでルールごとに推し機種が変わることを確認済み。例: 7のつく日だけ別格の店)
    rule_ms = pd.DataFrame()
    if sel_rules and not ms.empty:
        ph = ",".join("?" * len(sel_rules))
        rule_ms = q(f"""SELECT machine, MAX(mai) AS rule_mai, MAX(n_days) AS rule_days
                        FROM rule_machine_stats
                        WHERE hall=? AND rule IN ({ph}) GROUP BY machine""",
                    (hsel, *sel_rules))
        if not rule_ms.empty:
            ms = ms.merge(rule_ms, on="machine", how="left")
            # ルール実績が十分(3日以上)ある機種はそれを主指標に、無ければ全イベント平均のまま
            has = ms["rule_days"].fillna(0) >= 3
            ms.loc[has, "uplift_mai"] = ms.loc[has, "rule_mai"]
            ms["score"] = ms["uplift_mai"].fillna(-9999)
            ms = ms.sort_values("score", ascending=False)
            st.caption(f"「{('・'.join(sel_rules))}」の実績がある機種は、その日の成績で並べています。")
    daily = q("""SELECT date, machine, mai FROM hall_event_machine_daily
                 WHERE hall=?""", (hsel,))

    def chips(machine: str, k: int = 3) -> str:
        """直近イベント日ごとの台あたり差枚チップ (前回/前々回/3回前)."""
        d = daily[daily["machine"] == machine].sort_values("date", ascending=False).head(k)
        out = []
        for _, x in d.iterrows():
            col = "green" if x["mai"] >= 100 else ("red" if x["mai"] <= -100 else "gray")
            dd = dt.date.fromisoformat(x["date"])
            out.append(f"{dd.month}/{dd.day} :{col}[{x['mai']:+,.0f}]")
        return " ・ ".join(out)

    marks: list[tuple] = []
    if not ms.empty:
        used: set = set()
        top = ms[ms["uplift_mai"].notna()]
        # ◎本命=+300枚/台×5日以上、○対抗=+150枚×3日以上 (各最大3)。
        # ▲穴・新台は撤去: 実データで「新台ほど差枚が悪い」と反証された (導入0-14日 -454枚
        # → 121日+ +18枚)。店は新台に高設定を入れず回収装置にするため (2026-09-01検定)
        for _, r in top[(top["uplift_mai"] >= 300) & (top["n_days"] >= 5)].head(3).iterrows():
            marks.append(("◎", "本命", r))
            used.add(r["machine"])
        for _, r in top[(top["uplift_mai"] >= 150) & (top["n_days"] >= 3)
                        & (~top["machine"].isin(used))].head(3).iterrows():
            marks.append(("○", "対抗", r))
            used.add(r["machine"])
    if not marks:
        st.info("予想印を付けられるだけのイベント実績がまだありません (蓄積中)")
    elif not any(m == "◎" for m, _, _ in marks):
        st.info("この店にはまだ本命級 (+300枚/台クラス) の機種がありません。○対抗までです")
    for mark, label, r in marks:
        intro = "1月以前から" if r["machine_censored"] else f"{r['machine_first_seen']} 導入"
        # 「イベント日に上乗せされる」関係は前後半で繋がらなかった (r=+0.05) ので、
        # 検証を通った「この店でのその機種の地力 (出率)」を主表示にする (2026-09-01)
        pay_s = (f"この店での出玉率 {r['payout_recent']:.1f}%"
                 if not pd.isna(r.get("payout_recent")) else "実績少なめ")
        with st.container(border=True, key=f"card1_{_ci()}"):
            # 予想印はこの画面で一番先に目に入るべきものなので、
            # 太字だけでなく色付きのピルにする (2026-09-02)
            import html as _h
            _mk = "hon" if mark == "◎" else "tai"
            st.markdown(
                f'<div class="mk-line"><span class="mk mk-{_mk}">{mark} {label}</span>'
                f'<b>{_h.escape(str(r["machine"]))}</b>'
                f'<span class="mk-n">{int(r["n_units"])}台</span></div>',
                unsafe_allow_html=True)
            st.markdown(f"{pay_s} ・ {intro} ・ 稼働{int(r['machine_avg_games']):,}G/日  \n"
                        f"直近イベント: {chips(r['machine']) or 'データなし'}")
            # 中身が無いなら開かせない。以前は常に出していて、開くと
            # 「まだ足りません」だけが出た。実測で16.1%が空 (台番が1台しか
            # 無い機種は末尾・並びの比較ができないため必ず空になる)。
            # expander の中身は開閉に関わらず毎回評価されるので、
            # ここで先に計算しても負荷は変わらない (2026-09-02)。
            rec = recommend_units(hsel, r["machine"], sel_rules)
            if not rec.empty:
                with st.expander(f"台番ごとの過去の記録を見る ({evidence_badge('occult', True)})"):
                    track_once("unit_detail", r["machine"])
                    st.caption(
                        "⚠️ ここから下は**予想ではありません**。台番から次に出る台を当てられるか"
                        "検証しましたが、裏付けは取れませんでした（詳しくは「🔬 検証」）。"
                        "過去にどうだったかの記録として置いています。読み物としてどうぞ。")
                    st.dataframe(rec.style.map(color_mai, subset=["この店の平均より"])
                                 .format({"この店の平均より": "{:+,.0f}枚"}),
                                 width="stretch", hide_index=True)
                    st.caption("数字はこの店の過去の末尾・並びの位置ごとの差枚です。"
                               "同じ規模の差は、台番を無視してランダムに選んでも出ます。"
                               "設定はホールが決めるもので、当たりを保証するものではありません。")
    if marks:
        # 根拠の数字を、印そのもの (uplift_mai) の前向き検証に差し替えた (2026-09-04,
        # Issue #3 Phase A-10)。以前の「+187枚」は別指標 (機種の地力順位) の検証値で、
        # 印の根拠とずれていた。a10: 前半◎の機種 2,801件の後半中央値 +182枚 (非該当
        # +4枚)、陰性対照 +70±6枚、p=0.0005。ただし後半も◎級 (300枚超) は 36% で、
        # 「◎なら次も◎」ではない。7割はプラスで止まる
        st.caption(f"{evidence_badge('verified', True)}｜印は前向きに検証しています"
                   "（前半で◎だった機種は後半も1台あたり中央値+182枚、7割がプラス。"
                   "ただし後半も◎級だったのは36%）。  \n"
                   "◎=直近イベントで安定して上振れ ○=上振れ実績あり。"
                   "チップの数字=各イベント日の1台あたり差枚。"
                   "※新台は当データでは差枚が悪い傾向のため印には使いません。")

    # --- 📅 過去のイベント結果 ---
    # --- 🎯 ジャグラーの狙い台 (2026-09-01) ---
    # ジャグラーだけは台別に根拠のある傾向が出せる。出玉の振れ幅が小さく設定を
    # 推し量れること、島移動が少なく台番を追えることによる。AT機には出さない。
    jm = q("""SELECT DISTINCT machine FROM juggler_unit_score WHERE hall=?
              ORDER BY machine""", (hsel,))
    if not jm.empty:
        # 2026-09-05 (Issue #27): 5台をフラットに並べていたのを ◎一番手 / ○次点 に分けた。
        # 検証 (scripts/reverify/j1_unit_mark_resolution.py, 1,442組, perm 2,000回):
        #   [1] 陽性対照を先に取り、道具の分解能を確かめた。真に +0.20 設定高い台を
        #       注入すると 71.8% が rank1 に来る (偶然 7.9%)、+0.50 なら 98.6%。
        #       つまり上位の台どうしを見分ける力はある。
        #   [2] そのうえで実データを見ると、top5 と それ以外 は
        #       +22.0枚/日 (t=+4.38, perm p=0.0005) で本物。5台の選定は検証を通った。
        #   [3] ところが 5台の中の順位は rank1 − rank2-3 = -2.7枚/日 (p=0.77) でゼロ。
        #       店は特定の台番に持続的な高設定を置いていない。
        # → 選定は verified に格上げ、順位は「誤差の範囲」と明記したうえで出す。
        #   順位を出すのはユーザー裁定 (2026-09-05)。統計のブレでも一番手は欲しい、という
        #   要求で、選定自体に裏付けがある以上その上に乗せてよいと判断した。
        st.markdown("##### 🎯 ジャグラーの狙い台")
        st.caption(f"{evidence_badge('verified', True)}｜同じ店の同じ機種の中で、"
                   "過去に設定が高めだった台です。この**5台は店内平均より +14枚/日**、"
                   "それ以外の台は **-8枚/日** でした"
                   "（1,442組で前向きに検証。差 +22枚/日）。")
        for _i, mrow in jm.iterrows():
            ju = juggler_units(hsel, mrow["machine"])
            if ju.empty:
                continue
            good = ju[ju["dev_setting"] > 0].head(5)
            with st.container(border=True, key=f"card2_{_ci()}"):
                st.markdown(f"**{mrow['machine']}** ({int(ju['n_units'].iloc[0])}台を比較)")
                if good.empty:
                    st.caption("この機種では特に高めの台は見当たりません")
                else:
                    nos = [int(x) for x in good["unit_no"]]
                    st.markdown(
                        f'<div class="mk-line"><span class="mk mk-hon">◎ 一番手</span>'
                        f'<b>{nos[0]}番</b></div>', unsafe_allow_html=True)
                    if nos[1:]:
                        st.markdown(
                            f'<div class="mk-line"><span class="mk mk-tai">○ 次点</span>'
                            f'<b>{"、".join(f"{x}番" for x in nos[1:])}</b></div>',
                            unsafe_allow_html=True)
                    bad = ju[ju["dev_setting"] <= 0]["unit_no"].head(6).tolist()
                    if bad:
                        st.caption("低めだった台: "
                                   + "、".join(f"{int(x)}番" for x in bad)
                                   + ("…" if len(ju) > len(bad) + len(good) else ""))
        st.caption("◎は数字上のトップですが、**○との差は誤差の範囲**です"
                   "（前向きに測ると -2.7枚/日、p=0.77）。5台のどれに座っても期待値は"
                   "変わりません。迷ったときの目印として置いています。  \n"
                   "ジャグラーは出玉から設定を推し量れるので、この機種だけ出しています。"
                   "AT機は運の振れ幅が大きく、台ごとの良し悪しを数字で切り分けられませんでした。"
                   "詳しくは「🔬 検証」へ。")

    st.markdown("##### 📅 過去のイベント結果")
    evd = q("""SELECT report_id, date, mai FROM hall_event_daily
               WHERE hall=? ORDER BY date DESC LIMIT 12""", (hsel,))
    if evd.empty:
        st.info("イベント日データ蓄積中")
    else:
        rr = q(f"""SELECT report_id, GROUP_CONCAT(rule, '、') AS rules FROM report_rules
                   WHERE report_id IN ({','.join(['?'] * len(evd))}) GROUP BY report_id""",
               tuple(evd["report_id"]))
        rmap = dict(zip(rr["report_id"], rr["rules"])) if not rr.empty else {}
        for _, e in evd.iterrows():
            label = (f"{fmt_date(e['date'])} {rmap.get(e['report_id'], '')} ・ "
                     f"{e['mai']:+,.0f}枚/台")
            with st.expander(label):
                md = q("""SELECT machine AS 機種, n_units AS 台数,
                                 mai AS "枚/台", payout AS 出率
                          FROM event_report_machine WHERE report_id=?
                          ORDER BY mai DESC LIMIT 12""", (e["report_id"],))
                st.dataframe(md.style.map(color_mai, subset=["枚/台"])
                             .format({"枚/台": "{:+,.0f}", "出玉率": "{:.1f}"}),
                             width="stretch", hide_index=True)
        st.caption("タップでそのイベント日の機種別結果。枚/台=その日の1台あたり差枚。")

    st.markdown("##### 旧イベ実績 (同じ曜日の通常日と比較)")
    rs = q("""SELECT rule, uplift_pt, uplift_mai, p_shuffle, concentrated, n_event,
                     oos_state, oos_test_pt, oos_test_mai
              FROM hall_rule_stats WHERE hall=? ORDER BY uplift_mai DESC""", (hsel,))
    if rs.empty:
        st.info("実績データ蓄積中")
    else:
        # 信頼度は前向き検証ベース。枚/台も検証済みなら「後半で実際に出た値」を出す
        _oos = rs.apply(lambda r: stars_by_oos(r["oos_state"], r["oos_test_pt"]), axis=1)
        rs["信頼度"] = [x[0] or "—" for x in _oos]
        rs["判定"] = [x[1] for x in _oos]
        rs["枚/台"] = rs["oos_test_mai"].fillna(rs["uplift_mai"])
        view = rs.rename(columns={"rule": "旧イベ", "n_event": "イベント日数"})
        st.dataframe(view[["旧イベ", "枚/台", "判定", "信頼度", "イベント日数"]]
                     .style.map(color_mai, subset=["枚/台"]).format({"枚/台": "{:+,.0f}"}),
                     width="stretch", hide_index=True)
        n_ver = int((rs["oos_state"] == "both").sum())
        st.caption(
            "枚/台 = イベント日が通常日をどれだけ上回るかの1台あたり期待枚数。  \n"
            f"判定は**検証**の結果です（この店は {n_ver}/{len(rs)} 件が検証済み）。"
            "期間を前半と後半に分け、前半だけで見えた効きが後半のデータでも出たかを確認しています。"
            "「検証待ち」はイベント日が10日たまると判定できます。")

    st.markdown("##### 曜日の傾向 (通常営業)")
    wd = q("SELECT weekday, mai, n FROM hall_weekday_stats WHERE hall=? ORDER BY weekday", (hsel,))
    if wd.empty:
        st.info("曜日データ蓄積中")
    else:
        wd["曜日"] = wd["weekday"].map(lambda w: "月火水木金土日"[w])
        view = wd.set_index("曜日")[["mai"]].rename(columns={"mai": "枚/台"})
        st.dataframe(view.T.style.map(color_mai).format("{:+,.0f}"), width="stretch")
        st.caption("旧イベ日を除いた通常営業の曜日別・1台あたり平均差枚。緑の濃い曜日が"
                   "その店の普段の狙い目 (イベントと重なると更に期待)。")

    st.markdown("##### 機種の新旧の傾向 (イベント日)")
    age = q("SELECT bucket, mai FROM hall_age_stats WHERE hall=?", (hsel,))
    if age.empty:
        st.info("データ蓄積中 (期中導入機種が少ない店では出せません)")
    else:
        lab = {"new": "新台(〜14日)", "semi": "準新台(15〜60日)", "mature": "定着(120日〜)"}
        amap = dict(zip(age["bucket"], age["mai"]))
        view = pd.DataFrame([{"区分": lab[b], "枚/台": amap[b]}
                             for b in ["new", "semi", "mature"] if b in amap]).set_index("区分")
        st.dataframe(view.T.style.map(color_mai).format("{:+,.0f}"), width="stretch")
        st.caption("導入からの経過でその店がどこに設定を入れるか。全店平均は「新台ほど悪い」だが、"
                   "新台に高設定を入れる店も一部ある。緑の濃い区分がこの店の狙い目。")

    # 末尾・並びの端・隣の3節を1つの折りたたみに畳む (2026-09-04 レイアウト見直し)。
    # 店ごとに検定して「固める店」は見つからなかった (Issue #3 Phase A) ので、
    # 3節で約1,260px (スマホ1画面半) を占める独立見出しにしておく理由がない。
    # 数字は事実の記録として残す方針 (ユーザー判断) なので消さず、既定は閉じる。
    with st.expander(f"過去の位置別の記録 ｜ 末尾・並びの端・隣 ({evidence_badge('occult', True)})", expanded=False):
        st.caption("店ごとに検定しましたが、特定の位置に高設定を固める店は見つかっていません。"
                   "過去にどうだったかの記録です。次を予測する根拠にはなりません。")
        # 「狙い目の末尾」→「末尾別の出方」(2026-09-04, Issue #3 Phase A)。
        # 店ごとに検定して「末尾に固める店」は見つからなかった (ジャグラー推定設定、
        # 検出率90%の方法で 515店中ゼロ)。事実の記録として数字は残すが、
        # 「狙い目」と呼んで次を予測する根拠にはしない
        st.markdown("**末尾別の出方**")
        # 5つのラジオは横に並びきらず 3+2 で折り返して不格好だった。
        # ラベルの「直近」は見出し (集計期間) と重複するので落とし、
        # ナビと同じ segmented_control にして連続したチップに見せる (2026-09-02)。
        _PERIODS = {"30日": 30, "90日": 90, "180日": 180, "360日": 360, "全期間": 3650}
        t_period = st.segmented_control(
            "集計期間", list(_PERIODS), default="90日", key="tail_period")
        if t_period is None:      # 選択中を再タップして解除された場合
            t_period = "90日"
        st.caption("末尾ごとの実際の出方の記録です。全店を店ごとに検定しましたが、"
                   "特定の末尾に高設定を固める店は見つかりませんでした"
                   "（傾向Q&A参照）。過去にどの末尾が出たかは分かりますが、"
                   "次に同じ末尾が出る根拠にはなりません。"
                   "期間を短くすると鮮度は上がりますが、データは少なくなります。")
        t_days = _PERIODS[t_period]
        tails = q("""SELECT tail, ev_mai, ct_mai FROM hall_tail_period
                     WHERE hall=? AND days=?""", (hsel, t_days))
        if tails.empty or tails["ev_mai"].isna().all():
            st.info("末尾データ蓄積中")
        else:
            # 「イベント日に行くならどの末尾か」「通常日ならどの末尾か」は独立した2軸。
            # 差ではなく、それぞれの日の実際の台あたり平均差枚を並べる
            view = pd.DataFrame({"末尾": tails["tail"].astype(int),
                                 "イベント日 枚/台": tails["ev_mai"].round(0),
                                 "通常日 枚/台": tails["ct_mai"].round(0)}).sort_values("末尾")
            st.dataframe(view.style
                         .map(color_mai, subset=["イベント日 枚/台", "通常日 枚/台"])
                         .format({"イベント日 枚/台": "{:+,.0f}", "通常日 枚/台": "{:+,.0f}"}),
                         width="stretch", hide_index=True)
            st.caption("行く日のタイプの列だけを見て、緑の濃い末尾を狙う (2つの列は別々の狙い方)。"
                       "数字は実際の1台あたり平均差枚。")

        st.markdown("**機種の並びの端 (角台の目安)**")
        edge = q("""SELECT kind, edge_mai, mid_mai, edge_n, mid_n
                    FROM hall_edge_stats WHERE hall=?""", (hsel,))
        if edge.empty:
            st.info("データ蓄積中 (連番で並ぶ機種が少ない店では出せません)")
        else:
            lab = {"event": "イベント日", "control": "通常日"}
            rows = []
            for _, r in edge.iterrows():
                rows.append({"日": lab.get(r["kind"], r["kind"]),
                             "並びの端 枚/台": round(r["edge_mai"]),
                             "中の台 枚/台": round(r["mid_mai"])})
            ev = pd.DataFrame(rows).set_index("日").reindex(["イベント日", "通常日"]).dropna(how="all")
            st.dataframe(ev.style.map(color_mai, subset=["並びの端 枚/台", "中の台 枚/台"])
                         .format({"並びの端 枚/台": "{:+,.0f}", "中の台 枚/台": "{:+,.0f}"}),
                         width="stretch")
            st.caption("同じ機種が連番で並ぶ列の両端 と 中の台 の平均差枚。端の方が緑が濃い店は"
                       "並びの端に高設定を置く傾向。※これは「機種の並びの端」であり、島全体の"
                       "物理的な角台とは限りません (台番の並びからの推定)。")
            posdf = q("""SELECT kind, pos, mai, n FROM hall_edge_pos
                         WHERE hall=? ORDER BY kind, pos""", (hsel,))
            if not posdf.empty:
                with st.expander("端からの位置別 (角1・2・3… の偏り)"):
                    plab = {0: "角(端)", 1: "端から2番目", 2: "端から3番目",
                            3: "端から4番目", 4: "端から5番目以降"}
                    pv = posdf.copy()
                    pv["位置"] = pv["pos"].map(plab)
                    pv["日"] = pv["kind"].map({"event": "イベント日", "control": "通常日"})
                    tbl = pv.pivot_table(index="位置", columns="日", values="mai",
                                         sort=False).reindex(list(plab.values())).dropna(how="all")
                    st.dataframe(tbl.style.map(color_mai).format("{:+,.0f}", na_rep="—"),
                                 width="stretch")
                    st.caption("角から数えた位置ごとの平均差枚。角だけ甘い店/角寄りの数台に"
                               "散らす店/交互に置く店などの癖が見えます (長い並びのみ集計)。")

        st.markdown("**隣も高設定になりやすいか (連続投入)**")
        nb = q("""SELECT kind, p_cond, p_base FROM hall_neighbor_stats WHERE hall=?""", (hsel,))
        if nb.empty:
            st.info("データ蓄積中 (高設定台が少ない店では出せません)")
        else:
            lab = {"event": "イベント日", "control": "通常日"}
            rows = [{"日": lab.get(r["kind"], r["kind"]),
                     "隣も高設定の率": r["p_cond"], "店全体の高設定率": r["p_base"]}
                    for _, r in nb.iterrows()]
            nv = pd.DataFrame(rows).set_index("日").reindex(["イベント日", "通常日"]).dropna(how="all")
            st.dataframe(nv.style.format("{:.0%}", na_rep="—"), width="stretch")
            st.caption("高設定っぽい台 (出玉率105%+) の物理的な隣も高設定だった割合 vs 店全体の"
                       "高設定率。隣の率が全体より高い店は島に固めて入れる傾向 = 隣が良ければ"
                       "続けて座る価値あり。近い＝バラ置きで、隣は当てにならない。")


    # --- 近くの店 (2026-09-02) ---
    # 既にその店に来ている人は「隣は今日どうなのか」を知りたい。
    # 単独のページだと比較できず、行く前の判断にも使えなかった
    _me = halls[halls["hall"] == hsel]
    if not _me.empty and pd.notna(_me["lat"].iloc[0]):
        la, lo = float(_me["lat"].iloc[0]), float(_me["lon"].iloc[0])
        near = halls[halls["lat"].notna() & (halls["hall"] != hsel)].copy()
        near["km"] = near.apply(
            lambda r: haversine_km(la, lo, r["lat"], r["lon"]), axis=1)
        near = near[near["km"] <= 5].nsmallest(8, "km")
        if not near.empty:
            st.markdown("##### 近くの店")
            _tags = hall_tags(st.session_state.get("show_crowd_pill", False), _crowd_kind())
            for _, r in near.iterrows():
                sub = [f"{r['km']:.1f}km"]
                _j = q("""SELECT mean_setting FROM hall_juggler_stats
                          WHERE hall=? AND n_unit_days >= 100""", (r["hall"],))
                if not _j.empty:
                    sub.append(f"ジャグ予想 {_j['mean_setting'].iloc[0]:.2f}")
                _u = q("""SELECT avg_games FROM hall_util_stats
                          WHERE hall=? AND n_unitdays >= 100""", (r["hall"],))
                if not _u.empty:
                    sub.append(f"{_u['avg_games'].iloc[0]:,.0f}G")
                st.markdown(card_html(hall_url(r["hall"]), r["hall"],
                                      meta=" ・ ".join(sub),
                                      tag=_tags.get(r["hall"])),
                            unsafe_allow_html=True)
            st.caption("直線距離5km以内。タップで比較できます。"
                       "座標が取れている店だけを出しています。")

    # --- 全データ (閾値で切らず、この店の全項目・全数値を生で出す) ---
    with st.expander("🔍 この店の全データを見る (すべての数値)"):
        st.caption("上のセクションは要点だけを載せています。ここでは閾値で切らず、"
                   "この店について計算したすべての数値を出します。自分で判断したい方向け。")

        st.markdown("**旧イベ・周年 別の実績 (全ルール)**")
        a1 = q("""SELECT rule AS ルール, uplift_mai AS "枚/台", p_shuffle AS p値,
                         n_event AS イベ日数, n_control AS 通常日数
                  FROM hall_rule_stats WHERE hall=? ORDER BY uplift_mai DESC""", (hsel,))
        if not a1.empty:
            st.dataframe(a1, width="stretch", hide_index=True)
        else:
            st.caption("—")

        st.markdown("**曜日別 (通常営業、全7日)**")
        a2 = q("""SELECT weekday AS 曜日番号, mai AS "枚/台", n AS 台日数
                  FROM hall_weekday_stats WHERE hall=? ORDER BY weekday""", (hsel,))
        if not a2.empty:
            a2["曜日"] = a2["曜日番号"].map(lambda w: WD_JA[int(w)])
            st.dataframe(a2[["曜日", "枚/台", "台日数"]], width="stretch", hide_index=True)
        else:
            st.caption("—")

        st.markdown("**機種の新旧 (全3区分)**")
        a3 = q("""SELECT bucket AS 区分, mai AS "枚/台", n AS 台日数
                  FROM hall_age_stats WHERE hall=?""", (hsel,))
        if not a3.empty:
            a3["区分"] = a3["区分"].map({"new": "新台(〜14日)", "semi": "準新台(15〜60日)",
                                        "mature": "定番(120日〜)"}).fillna(a3["区分"])
            st.dataframe(a3, width="stretch", hide_index=True)
        else:
            st.caption("—")

        st.markdown("**機種ジャンル別 (全ジャンル)**")
        a4 = q("""SELECT genre AS ジャンル, mai AS "枚/台", n AS 台日数
                  FROM hall_genre_stats WHERE hall=? ORDER BY mai DESC""", (hsel,))
        if not a4.empty:
            st.dataframe(a4, width="stretch", hide_index=True)
        else:
            st.caption("—")

        st.markdown("**並びの位置別 (角からの距離)**")
        a5 = q("""SELECT kind AS 日, pos AS 端からの距離, mai AS "枚/台", n AS 台日数
                  FROM hall_edge_pos WHERE hall=? ORDER BY kind, pos""", (hsel,))
        if not a5.empty:
            a5["日"] = a5["日"].map({"event": "イベント日", "control": "通常日"}).fillna(a5["日"])
            st.dataframe(a5, width="stretch", hide_index=True)
        else:
            st.caption("—")

        st.markdown("**端 vs 中 / 連続投入 / 配分スタイル (生値)**")
        a6 = q("""SELECT kind, edge_mai, mid_mai, edge_n, mid_n FROM hall_edge_stats
                  WHERE hall=?""", (hsel,))
        a7 = q("""SELECT kind, p_cond, p_base, n_good_left, n_total
                  FROM hall_neighbor_stats WHERE hall=?""", (hsel,))
        a8 = q("""SELECT concentration AS メリハリ度, n_days AS 集計日数,
                         recent_mai AS "直近60日 枚/台", past_mai AS "以前 枚/台",
                         trend AS 変化 FROM hall_style_stats WHERE hall=?""", (hsel,))
        for label, df in (("端 vs 中", a6), ("連続投入", a7), ("配分スタイル", a8)):
            st.caption(label)
            if not df.empty:
                st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.caption("—")

        st.markdown("**イベント日に強い機種 (全機種、上位30)**")
        a9 = q("""SELECT machine AS 機種, n_units AS 台数, n_days AS 日数,
                         payout_recent AS 直近出率, uplift_mai AS "枚/台",
                         machine_first_seen AS 導入日, machine_avg_games AS 稼働G
                  FROM machine_event_score WHERE hall=? AND present=1
                  ORDER BY score DESC LIMIT 30""", (hsel,))
        if not a9.empty:
            st.dataframe(a9, width="stretch", hide_index=True)
        else:
            st.caption("—")

# ---------- ④ 傾向Q&A ----------

if page == "傾向":
    # 読み物3つを1ページのタブに (2026-09-04)。中身は変えていない。
    # 主ナビ (白ピル) の直下に同じピルを並べると階層が消えるので、副ナビは
    # st.tabs の下線様式のまま、視認性だけ CSS で上げる (ユーザー指摘)。
    # ページ見出しは置かない (「読みもの」はダサい、というユーザー指摘で撤回)。
    # タブの文字を一段大きくして、タブ自体にページ見出しを兼ねさせる。
    # ピル (主) と下線タブ (副) で様式が違うので、見出しが無くても階層は読める
    # 副ナビは白ピル (segmented_control)。主ナビが下線タブになったので2段重ねにならない
    # default= は持たない。?sub= で session_state に先に入れることがあり、default と
    # 併用すると Streamlit が二重指定として拒む。未選択 (None) は下で「傾向」に落とす
    _sub = st.segmented_control("読みもの", ["傾向", "検証", "このサイト"],
                                key="sub_page", label_visibility="collapsed")
    if _sub is None:          # 初回、または選択中を再タップして解除された場合
        _sub = "傾向"
    if _sub == "傾向":
        st.markdown("##### 📊 データでわかる傾向")
        st.caption("よくある俗説を、当サイトの全店データ (東京・神奈川) で検証した答えです。"
                   "数字は毎晩自動更新。※全店平均の傾向で、狙い目は店ごとに異なります。")
        try:
            for item in trend_qa():
                with st.container(border=True, key=f"card3_{_ci()}"):
                    st.markdown(f"**Q. {item['q']}**  \n➡️ **{item['a']}**")
                    st.caption(item["d"])
        except Exception as e:
            st.info(f"傾向を計算できませんでした (データ蓄積中)。{str(e)[:60]}")

    # ---------- ⑤ 検証したこと (通ったもの・通らなかったものを両方出す) ----------

    if _sub == "検証":
        st.markdown("##### 🔬 検証したこと・できなかったこと")
        st.caption("「効きます」と書く以上、どう確かめたかを全部出します。"
                   "通らなかった検証も同じだけ載せます。そちらのほうが多いです。")

        with st.container(border=True, key=f"card4_{_ci()}"):
            st.markdown("**どうやって確かめているか**")
            st.markdown(
                "過去のデータで一番よく当たる法則を探すと、必ず何か見つかります。"
                "偶然そう見えただけのものが混ざるからです。そこで**期間を前半と後半に切り、"
                "前半だけを見て法則を決め、後半のデータで実際に効いたかを答え合わせ**します。"
                "後半は法則を作るときに一切見ていないので、ここで効けば本物に近い。  \n\n"
                "さらに**わざと無意味な選び方（ランダム）と勝負させます**。"
                "ランダムに選んでも同じくらい良い結果が出るなら、その法則には価値がありません。")

        st.markdown("**◎ 検証を通ったもの — 表示に使っています**")
        for title, body in [
            ("店 × イベント日 (通常日より出るか)",
             "前半で「効く」と判定した店×イベント日は、後半でも**1台あたり平均+63枚**"
             "（統計量 t=+9.87）。前半で上位10%に入った組は後半で**+131枚**。"
             "前半でマイナス判定だった組は後半も伸びず、選別がちゃんと効いています。"),
            ("機種 (その店で何を打つか)",
             "その店で出玉率が高い機種は次の期間も高い（相関 r=+0.41）。"
             "前半で上位2割だった機種は後半で**+187枚相当**（t=+13.38）。"),
        ]:
            with st.container(border=True, key=f"card5_{_ci()}"):
                st.markdown(f"{evidence_badge('verified')} **{title}**")
                st.markdown(body)

        st.markdown("**○ 参考どまり — 数字は出しますが弱いです**")
        with st.container(border=True, key=f"card6_{_ci()}"):
            st.markdown(f"{evidence_badge('occult')} **機種のイベント日の伸び**")
            st.markdown(
                "「この機種はイベント日に多く出る」という関係は、前半と後半で"
                "ほとんど繋がりませんでした（相関 r=+0.05）。続くのは機種そのものの"
                "機種そのものの強さのほうです。そのため機種は**出玉率順**で並べています。")

        st.markdown("**◎ ジャグラーの台選びだけは、根拠が取れました**")
        with st.container(border=True, key=f"card7_{_ci()}"):
            st.markdown(f"{evidence_badge('verified')} **ジャグラーの台別の傾向**")
            st.markdown(
                "スロットの出玉は運の振れ幅がとても大きく、"
                "**AT機では「設定が良かったのか、たまたま出たのか」を数字から切り分けられません**。"
                "ジャグラーは振れ幅が小さいぶん、同じデータ量でも設定を推し量る精度が上がります。"
                "さらにジャグラーは島の入れ替えが少なく、同じ台番を長く追いかけられます。  \n\n"
                "この2点のおかげで、ジャグラーに限れば**前半で良かった台は後半もやや良い**という"
                "関係を確認できました（同じ店の同じ機種の中で比べ、島の入れ替えも考慮した結果）。"
                "1,442組で答え合わせをすると、**選んだ5台は店内平均より +14枚/日**、"
                "それ以外の台は **-8枚/日**。差は +22枚/日でした。  \n\n"
                "**ただし、その5台の中の順位には意味がありません。** ◎と○の差を同じやり方で"
                "測ると **-2.7枚/日**（差なし）。◎は数字上のトップというだけで、"
                "5台のどれに座っても期待値は変わりません。")
            st.caption("順位が出ないのは道具のせいではありません。人工的に"
                       "「本当に +0.2 設定高い台」を混ぜると 71.8% で1位に来ます"
                       "（偶然なら 7.9%）。それだけの分解能で測ってなお差が出ない＝"
                       "店が特定の台番に高設定を置き続けてはいない、ということです。  \n"
                       "また、店・機種の違いを取り除く前は10倍近い差に見えました。"
                       "その大半は「甘い店かどうか」「どのジャグラーか」の差で、"
                       "台そのものの差ではありませんでした。")

        st.markdown("**▲ 裏付けが取れなかったもの — 予想には使いません**")
        for title, body in [
            ("台番の末尾 (末尾7が甘い、など)",
             "**決着しませんでした。** 前半で「良い」と判定した末尾を後半で試すと"
             "−3.7枚（95%信頼区間 −14.5〜+7.1枚）。ただしこの方法には十分な精度が無く、"
             "**1日55枚ぶんの効果を人工的に混ぜても検出できません**でした。"
             "つまり「効かない」ではなく「この方法では分からない」が正確です。"),
            ("角台・並びの位置",
             "見かけ上は良く見えます。しかし角台は**他より1日400ゲームほど多く回されており**、"
             "たくさん回された台ほど数字が良く見えるという性質があります。"
             "回転数を揃えて比べ直すと優位は消え、しっかり打ち込む帯（5,000ゲーム以上）では"
             "むしろ角のほうが悪くなりました。"),
            ("「そろそろ高設定が入る番」説",
             "しばらく高設定が入っていない台が狙い目か。**逆でした。**"
             "同じ台が繰り返し上位に来る傾向のほうが強く、"
             "同じ日・同じ機種の他の台と比べた差は**+1〜3枚**しかありません。"),
        ]:
            with st.container(border=True, key=f"card8_{_ci()}"):
                st.markdown(f"{evidence_badge('occult')} **{title}**")
                st.markdown(body)

        with st.container(border=True, key=f"card9_{_ci()}"):
            st.markdown("**★の意味 (2026-09-01 に定義を変えました)**")
            st.markdown(
                "以前の★は「過去の集計がどれくらい偶然っぽくないか」で付けていました。"
                "これは**過去を説明する力**であって、次も効くかには答えていません。  \n\n"
                "今の★は**検証の結果**です。期間を前半と後半に分け、"
                "前半だけを見て判定し、後半のデータで答え合わせします。")
            st.markdown(
                "| 判定 | 意味 | 後半で実際に出た数字 |\n|---|---|---|\n"
                "| 検証済み ★3〜5 | 前半・後半とも通常日を上回った | **+0.875pt（約+97枚/台）** |\n"
                "| ムラあり ★2 | 片方の期間だけ上回った | −0.150pt |\n"
                "| 低調 ★1 | どちらの期間も上回らなかった | −0.638pt |\n"
                "| 検証待ち ★なし | 前後半に分けるだけのイベント日がまだ無い | — |")
            st.caption("現在 552件中 234件（42.4%）が判定済み。残りはイベント日が10日たまり次第。"
                       "「検証済み」と「ムラあり」で +0.875pt と −0.150pt。"
                       "★が付いているかどうかを最初に見てください。")

        with st.container(border=True, key=f"card10_{_ci()}"):
            st.markdown("**なぜ台番の記録を消さないのか**")
            st.markdown(
                "当てられないと分かったからといって、事実まで消す必要はないと考えています。"
                "競馬新聞の予想印が当たりを保証しないのと同じで、"
                "材料が並んでいて、自分で読む余地があることに価値がある。  \n\n"
                "だから台番の過去の記録は**▲参考・裏付けなし**として残します。"
                "◎と▲がひと目で区別できれば、どこまで数字を信じるかはあなたが決められます。"
                "オカルトは大歓迎です。ただし**オカルトを統計だと偽ることはしません**。")

        with st.container(border=True, key=f"card11_{_ci()}"):
            st.markdown("**選ぶ順番が、そのまま効果の大きさです**")
            st.markdown(
                "| 選ぶもの | 効き方 |\n|---|---|\n"
                "| **どの店に行くか** | いちばん大きい |\n"
                "| **その店で何を打つか** | 同じくらい大きい |\n"
                "| **いつ行くか（イベント日）** | 大きい |\n"
                "| どの台に座るか（ジャグラー） | ごく小さい |\n"
                "| どの台に座るか（AT機・末尾・角） | 確かめられず |")
            st.caption("店選びと機種選びで大半が決まります。台選びに悩む時間があるなら、"
                       "行く店を見直したほうが効きます。")

        st.caption("検証に使った全データ: 通常日 270万台日 / イベント日 287万台日 "
                   "(東京・神奈川)。検証コードはリポジトリで公開しています。"
                   "数字の詳細を知りたい方向けに、検証スクリプトと結果を GitHub に置いています。")

    # ---------- ⑥ このサイトについて (運営方針・問い合わせ窓口・免責) ----------

    if _sub == "このサイト":
        st.markdown("##### ℹ️ このサイトについて")
        st.markdown("**勘に頼らない。でも、勘を否定しない。**")

        with st.container(border=True, key=f"card12_{_ci()}"):
            st.markdown("**何をしているサイトか**")
            st.markdown(
                "各ホールが公開している出玉データを集めて、統計として整理しています。  \n"
                "「この店のこの日は普段よりよく出ている」「この店ではこの機種が強い」といった"
                "傾向を、**過去のデータで確かめたうえで**お出ししています。  \n\n"
                "**当てることは目的にしていません。** スロットは運の振れ幅がとても大きく、"
                "誰にも当てられません。できるのは、事実を並べて、選ぶ材料をお渡しすることだけです。"
                "そこから先はご自身の勘で決めてください。オカルトは大歓迎です。")

        with st.container(border=True, key=f"card14n_{_ci()}"):
            # 「近所の店が出てこない＝サイトの不備」と読まれるのを防ぐ。
            # 掲載できない理由を先に言っておく (2026-09-03)
            st.markdown("**載っていない店について**")
            _nd = len(nodata_halls())
            st.markdown(
                f"分析できるのは、台ごとの差枚が公開されている **{len(halls):,}店** だけです。  \n"
                f"店はあるのに差枚が公開されていない店が **{_nd:,}店** あり、"
                "こちらは「店舗」の検索で店名を入れると、開店時刻・抽選・交換率などの"
                "店舗情報だけをご覧いただけます。  \n\n"
                "**どちらにも無い店もあります。** 掲載が無いことは、店が無いことでも、"
                "その店が悪いことでも、ありません。")

        with st.container(border=True, key=f"card13_{_ci()}"):
            st.markdown("**数字の信じ方 — ◎ ○ ▲ の意味**")
            st.markdown(
                f"| 印 | 意味 |\n|---|---|\n"
                f"| {EVIDENCE['verified'][0]} {EVIDENCE['verified'][1]} | "
                "期間を前半と後半に分け、前半だけで判定したものが後半でも当たっていた |\n"
                f"| {EVIDENCE['small'][0]} {EVIDENCE['small'][1]} | "
                "同じやり方で確かめたが、差はごくわずか |\n"
                f"| {EVIDENCE['weak'][0]} {EVIDENCE['weak'][1]} | "
                "過去の集計では出ているが、次も続くかは確かめられていない |\n"
                f"| {EVIDENCE['occult'][0]} {EVIDENCE['occult'][1]} | "
                "確かめたが裏付けは取れなかった。過去の記録として置いている |")
            st.caption("何をどう確かめたかは「🔬 検証」に全部書いています。"
                       "通らなかった検証もそのまま載せています。")

        with st.container(border=True, key=f"card14_{_ci()}"):
            st.markdown("**データの出どころ**")
            st.markdown(
                "- [みんレポ](https://min-repo.com/) — 各ホールの台ごとの出玉データ  \n"
                "- [みんパチ](https://minpachi.com/) — 店舗情報・旧イベント日  \n"
                "- 機種ごとの設定別の機械割は各メーカーの公表値")
            st.caption("いずれも一般に公開されている情報です。"
                       "当サイトはそれらを集計・統計処理した結果を掲載しています。")

        st.markdown("##### 📮 お問い合わせ")
        with st.container(border=True, key=f"card15_{_ci()}"):
            st.markdown("**データの誤りのご指摘**")
            st.markdown(f"[{CONTACT}](mailto:{CONTACT}?subject=データの誤りについて)")
            st.caption("店名・日付・機種など、どの数字かが分かる形でお知らせいただけると助かります。")
        with st.container(border=True, key=f"card16_{_ci()}"):
            st.markdown("**店舗関係者の方へ — 掲載内容に関するご連絡**")
            st.markdown(f"[{CONTACT}](mailto:{CONTACT}?subject=掲載内容について（店舗関係者）)")
            st.markdown(
                "掲載している数字は、各サイトで公開されている出玉データを集計したものです。"
                "内容に誤りがある場合や、掲載についてご相談がある場合は、"
                "**個別にお話をうかがったうえで、できる範囲で対応いたします**。"
                "まずはご連絡ください。")
        with st.container(border=True, key=f"card17_{_ci()}"):
            st.markdown("**その他のお問い合わせ**")
            st.markdown(f"[{CONTACT}](mailto:{CONTACT})")

        with st.container(border=True, key=f"card18_{_ci()}"):
            st.markdown("**利用状況の計測について**")
            st.markdown(
                "どの画面が使われているかを把握するため、**画面の表示と操作の記録**を取っています。"
                "サイトを改善するためのもので、**個人を特定できる情報は記録していません**"
                "（氏名・メールアドレス・IPアドレスなどは取得していません）。")

        st.markdown("##### ⚠️ ご利用にあたって")
        with st.container(border=True, key=f"card19_{_ci()}"):
            st.markdown(
                "- 掲載内容は過去のデータにもとづく**参考情報**です。勝敗や出玉を保証するものでは"
                "ありません  \n"
                "- 設定や出玉はホールが決定します。当サイトはホールの運営に一切関与していません  \n"
                "- 遊技は**余裕をもって、生活に支障のない範囲で**お楽しみください。"
                "取り返そうとして深追いすると損失が膨らみます  \n"
                "- ご利用は自己責任でお願いします")
            st.caption("のめり込みでお困りの方の相談窓口: "
                       "[リカバリーサポート・ネットワーク](https://rsn-sakura.jp/) "
                       "（ぱちんこ依存問題相談機関）")

    # ---------- 説明 (2026-09-01 に最下部へ移動) ----------
    # 参照用の説明であって毎回最初に読むものではない。画面の先頭に置くと、
    # 本題のカードが画面下半分にしか出てこない。読みたい人だけ開けばよい。
st.divider()
with st.expander("📐 この数字は何なの? (計算のしかた)"):
    st.markdown(
        "**元になっているデータ**  \n"
        "パチスロ店が公開している「その日・その台が何枚出したか(差枚)」の記録です。"
        "東京・神奈川の約370店・1台1日ごとの実績を毎晩集めています。\n\n"
        "**枚/台 の意味**  \n"
        "「1台あたり平均で何枚出たか」です。+300枚なら、その条件の台に座った人が"
        "平均して300枚浮いた、ということ。マイナスなら負けています。\n\n"
        "**「通常日より+◯枚」の出し方**  \n"
        "イベント日の成績を、**同じ店の普通の日**と比べた差です。店ごとの普段の出しやすさの違いを"
        "打ち消すため、他店とは比べません。\n\n"
        "**★ 信頼度**  \n"
        "その数字がどれだけ確かかを5段階で表します。データが多く、長期間ブレていないほど"
        "★が増えます。★が少ない数字は「たまたま」かもしれません。\n\n"
        "**やっていないこと**  \n"
        "店から広告費をもらって順位を上げる、といったことは一切ありません。"
        "全店を同じ計算式で採点しています。")
with st.expander("ℹ️ 用語・バッジの見かた"):
    st.markdown(
        "- **🗓 旧イベ**: 広告規制 (2011〜12年) の前にその店がイベントをやっていた日 (例: 7のつく日)。"
        "今も出やすい傾向が残る店が多く、当サイトは実データでその傾向を検証しています\n"
        "- **🎯末尾N**: イベント日にその末尾の台が明確に上振れる店\n"
        "- **📍角(並びの端)**: 機種の並びの端が甘い店 / **🔗連続投入**: 高設定の隣も高い店\n"
        "- **📅N曜**: 特定の曜日が甘い店 / **🎰ジャグラー・⚡AT機**: どちらに設定が入るか\n"
        "- **🆕新台・🔥準新台・🎖️定番機種**: 導入からの経過でどこに設定が入るか\n"
        "バッジは**特に強い特徴 (強さ「高」) だけ**に付きます。「中」程度の傾向は"
        "店舗情報の『この店の特徴 まとめ』で確認できます。")

# エリアを絞っているときだけ、その地域のデータがない店を末尾に出す。
# 「近所の店が載っていない」を防ぐ (2026-09-03 ユーザー方針)
if (page in ("本日", "イベント", "ランキング", "店舗")
        and not st.session_state.get("hall_pick")):
    nodata_section()

# ---------- 共通フッター (出典・免責) ----------
st.divider()
st.caption(
    "データ出典: [みんレポ](https://min-repo.com/) (差枚データ) / "
    "[みんパチ](https://minpachi.com/) (店舗情報・旧イベント日)。"
    "各サイトの公開データを統計処理したものです。  \n"
    "掲載内容は過去データにもとづく参考情報であり、勝敗・出玉を保証するものではありません。"
    "設定・出玉はホールが決定します。ご利用は自己責任でお願いします。  \n"
    f"📮 お問い合わせ・データの誤りのご指摘・**店舗関係者の方からのご連絡**: "
    f"[{CONTACT}](mailto:{CONTACT})  \n"
    "サイトの方針・確かめ方は「ℹ️ このサイトについて」をご覧ください。"
)

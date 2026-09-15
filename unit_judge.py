"""台ごとの設定判別 (#163).

ホールで打っている台の「G数 / BIG / REG」から、設定ごとの確率・期待機械割・期待時給を出す。
入力値は保存しない (データサイト由来の数字を集める側に回らないため)。

公開アプリ (satoru2111/hallscore-app) にも運ぶので、app_v2.py から import できるよう
リポジトリ直下に置いている。`src/` は公開リポに入れない (scripts/sync_public_app.py)。

## 事前分布 — 機種ごとの実測配分 (C)。一様や店の平均設定からの配分は使わない

`scripts/build_machine_setting_mix.py` が unit_bonus の BIG/REG から機種ごとの設定配分を EM で推定し、
`machine_setting_mix` テーブルに書く。アプリはそれを事前分布にする。

- 🚨 一様 (A) は使わない。実測の BIG/REG はどの機種も設定2〜3相当で、一様だと
  **台のデータが何も無くても期待時給がプラスに出る** (マイジャグラーV で +930円/時 = 設定1〜6の時給の単純平均)
- 🚨 店の平均設定から作る配分 (B、maxent_prior) も使わない。一様より当たるが、改善の大半は
  「低めに見る」効果で、画面の期待時給は実測の平均 −422円/時 に対して +232円/時 と甘かった
  (PR #165 のマージ前レビュー、docs/unit_judge_prior_20260914.md)。maxent_prior は検証の比較用に残す
- 配分が無い機種 (ハナハナ: unit_bonus に行が無い) は、設定4以上の確率と期待時給を出さない

## 期待時給

750G/時 × 3枚/G × (機械割 − 1) を設定ごとに円へ換算し、事後確率で平均する。
750G は jugglersnet の時給まとめと同じ前提で、マイジャグラー5 の設定1 −1,350円 / 設定6 +4,230円 (20円)
を再現する (tests/test_unit_judge.py)。

換算は非対称: **増えるメダルは換金単価、減るメダルは max(20円, 換金単価)**。
非等価の店で負けを換金単価で数えると、負けが小さく見えるため。
⚠️ それでも **上限寄り** の値。実際には打ち始めに現金で借りたメダルを換金単価でしか戻せない分
(借りた枚数 × (20円 − 換金単価)) が損になるが、それは入れていない (2回目のレビュー)。
交換率は 4.5〜7.5枚 の外を「データの誤り」とみなし、全店の中央値を使う
(DB には 30枚・460枚・4000枚 などが入っている)。
**設定の見込みだけの期待値で、その日の引きのぶれは入っていない。**
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SPECS_PATH = Path(__file__).parent / "unit_judge_specs.json"

# 1台で入力できる現実的な上限。打ち間違い (桁の打ち損じ) を計算に通さないため
MAX_GAMES = 12000
# ボーナス合算がこれより軽いのはスペック上ありえない (最も軽い設定でも 1/114)。
# ただし打ち始めは軽い合算が普通に起きる (60G でボーナス2回は 1/114 でも約9%) ので、
# CHECK_COMBINED_FROM G 以上のときだけ判定する
MIN_GAMES_PER_BONUS = 40
CHECK_COMBINED_FROM = 500
# 貸しメダルの単価 (1枚 20円)。負けの換算に使う
LOAN_YEN_PER_COIN = 20.0
# 交換率 (100円あたりの枚数) として受け入れる範囲。外は誤りとみなす
EXCHANGE_MAI_RANGE = (4.5, 7.5)
_DIGITS = re.compile(r"[0-9]{1,6}")


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


@dataclass(frozen=True)
class Spec:
    key: str
    group: str
    settings: tuple[str, ...]
    bb: tuple[float, ...]
    rb: tuple[float, ...]
    wari: tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.settings)

    def high_from(self) -> int:
        """「設定4以上」の開始位置 (0 始まり)。5段階の機種でも 4 と V を高設定とする."""
        return 3


def load_specs(path: Path = SPECS_PATH) -> tuple[dict[str, Spec], dict[str, str], dict]:
    """スペック表を読む.

    返り値: (key → Spec, 正規化した DB 機種名 → key, _meta)
    """
    raw = json.loads(Path(path).read_text())
    specs: dict[str, Spec] = {}
    alias: dict[str, str] = {}
    for m in raw["machines"]:
        n = len(m["settings"])
        for col in ("bb", "rb", "combined", "wari"):
            if len(m[col]) != n:
                raise ValueError(f"{m['key']}: {col} の長さ {len(m[col])} が設定数 {n} と違う")
        specs[m["key"]] = Spec(m["key"], m["group"], tuple(m["settings"]),
                               tuple(float(x) for x in m["bb"]),
                               tuple(float(x) for x in m["rb"]),
                               tuple(float(x) for x in m["wari"]))
        for a in m["aliases"]:
            k = nfkc(a)
            if k in alias and alias[k] != m["key"]:
                raise ValueError(f"機種名 {a} が {alias[k]} と {m['key']} の両方に登録されている")
            alias[k] = m["key"]
    return specs, alias, raw["_meta"]


def spec_for(machine: str, specs: dict[str, Spec], alias: dict[str, str]) -> Spec | None:
    """DB の機種名 (または key) からスペックを引く。部分一致はしない (別機種を拾うため)."""
    k = nfkc(machine)
    if k in alias:
        return specs[alias[k]]
    return specs.get(machine)


def cell_text(v) -> str:
    """data_editor のセル値を文字列にする。欠損 (None / NaN / pd.NA) は空文字.

    pandas 3 では dtype=str の列で「+」で足した行の空欄が NaN になり、`str(v or "")` だと
    NaN が真として "nan" になった (2回目のレビュー)。pandas に依存せず判定する。
    """
    if v is None:
        return ""
    try:
        if v != v:          # NaN
            return ""
    except (TypeError, ValueError):   # pd.NA は比較で例外
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "<na>", "none") else s


def parse_count(text: str) -> int:
    """画面の入力文字列を回数にする。全角数字・桁区切りのカンマは受ける.

    `str.isdigit()` は使わない。超長い数字列や、isdigit は通るが int() が拒む文字
    (上付き数字など) で例外になり、ページごと落ちた (PR #165 レビュー)。
    """
    s = nfkc(text).replace(",", "")
    if not _DIGITS.fullmatch(s):
        raise ValueError("0 以上の整数 (6桁まで) で入れてください")
    return int(s)


def validate_counts(games: int, bb: int, rb: int) -> None:
    """入力の検査。ありえない値は計算に通さず ValueError にする."""
    for name, v in (("G数", games), ("BIG", bb), ("REG", rb)):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{name} は整数で入力してください")
        if v < 0:
            raise ValueError(f"{name} がマイナスです")
    if games <= 0:
        raise ValueError("G数が0です")
    if games > MAX_GAMES:
        raise ValueError(f"G数 {games} は1日の上限 ({MAX_GAMES}) を超えています")
    if (games >= CHECK_COMBINED_FROM and bb + rb > 0
            and games / (bb + rb) < MIN_GAMES_PER_BONUS):
        raise ValueError(f"ボーナス合算 1/{games / (bb + rb):.0f} はスペック上ありえません")


def log_likelihood(bb: int, rb: int, games: int, spec: Spec) -> list[float]:
    """設定ごとの対数尤度 (BIG と REG を独立なポアソンとみなす。定数項は落とす).

    差枚は使わない。差枚は BIG/REG 回数でほぼ決まるので、併用すると同じ情報を二度数える
    (engine_v0.setting_posterior_bonus と同じ考え方)。
    """
    out = []
    for nb, nr in zip(spec.bb, spec.rb):
        lb, lr = games / nb, games / nr
        out.append(bb * math.log(lb) - lb + rb * math.log(lr) - lr)
    return out


def _normalize_log(logw: list[float]) -> list[float]:
    mx = max(logw)
    w = [math.exp(v - mx) for v in logw]
    z = sum(w)
    return [x / z for x in w]


def uniform_prior(n: int) -> list[float]:
    return [1.0 / n] * n


def maxent_prior(mean: float, n: int = 6) -> list[float]:
    """平均設定が mean になる最大エントロピー配分 (p_s ∝ exp(θ·s), s = 1..n).

    ⚠️ アプリでは使わない (甘く出るため。モジュール冒頭参照)。検証の比較用 (B)。
    mean は (1, n) の内側に丸める。端ちょうどは一点集中になり、判別結果を
    データと無関係に固定してしまうため。
    """
    if mean is None or not math.isfinite(mean):
        raise ValueError("平均設定が数値ではありません")
    lo, hi = 1.0 + 1e-3, n - 1e-3
    target = min(max(mean, lo), hi)
    s = list(range(1, n + 1))

    def mean_of(theta: float) -> float:
        w = _normalize_log([theta * x for x in s])
        return sum(p * x for p, x in zip(w, s))

    a, b = -50.0, 50.0
    for _ in range(200):
        mid = (a + b) / 2
        if mean_of(mid) < target:
            a = mid
        else:
            b = mid
    return _normalize_log([((a + b) / 2) * x for x in s])


def posterior(bb: int, rb: int, games: int, spec: Spec,
              prior: list[float] | None = None) -> list[float]:
    """設定ごとの事後確率。prior を省略すると一様 (設定別の確率の参考表示用)."""
    validate_counts(games, bb, rb)
    prior = prior or uniform_prior(spec.n)
    if len(prior) != spec.n:
        raise ValueError(f"事前分布の長さ {len(prior)} が設定数 {spec.n} と違う")
    ll = log_likelihood(bb, rb, games, spec)
    return _normalize_log([l + math.log(max(p, 1e-300)) for l, p in zip(ll, prior)])


def expected_wari(post: list[float], spec: Spec) -> float:
    return sum(p * w for p, w in zip(post, spec.wari))


def wari_interval(post: list[float], spec: Spec, mass: float = 0.8) -> tuple[float, float]:
    """設定の見込みの幅を機械割で表す (事後分布の中央 mass 分に入る設定の機械割の範囲).

    機械割は設定の順に単調増加なので、設定の分位点がそのまま機械割の分位点になる。
    """
    tail = (1 - mass) / 2
    cum, lo_i, hi_i = 0.0, None, None
    for i, p in enumerate(post):
        cum += p
        if lo_i is None and cum > tail:
            lo_i = i
        if hi_i is None and cum >= 1 - tail:
            hi_i = i
    return spec.wari[lo_i], spec.wari[hi_i if hi_i is not None else spec.n - 1]


def hourly_yen(wari: float, win_yen_per_coin: float = LOAN_YEN_PER_COIN,
               loss_yen_per_coin: float | None = None, games_per_hour: int = 750,
               coin_per_game: int = 3) -> float:
    """1つの機械割に対する時給 (円)。増えるメダルは換金単価、減るメダルは max(貸し単価, 換金単価).

    減る側を max にするのは、換金単価が 20円を超える店 (4.6〜4.9枚 = 46枚貸し等) で
    勝ちだけ高く数えて甘くならないため。
    """
    if loss_yen_per_coin is None:
        loss_yen_per_coin = max(LOAN_YEN_PER_COIN, win_yen_per_coin)
    coins = games_per_hour * coin_per_game * (wari / 100.0 - 1.0)
    return coins * (win_yen_per_coin if coins > 0 else loss_yen_per_coin)


def expected_hourly(post: list[float], spec: Spec, win_yen_per_coin: float) -> float:
    """期待時給。換算が非対称なので、機械割を平均してから換算せず、設定ごとに換算して平均する."""
    return sum(p * hourly_yen(w, win_yen_per_coin) for p, w in zip(post, spec.wari))


def valid_exchange(mai_per_100yen: float | None) -> bool:
    lo, hi = EXCHANGE_MAI_RANGE
    return (mai_per_100yen is not None and math.isfinite(mai_per_100yen)
            and lo <= mai_per_100yen <= hi)


def yen_per_coin_from_exchange(mai_per_100yen: float | None,
                               default_mai: float | None = None) -> float:
    """交換率 (100円あたりの枚数) → 換金単価 (1枚あたりの円).

    範囲外・不明なら default_mai (アプリは全店の中央値を渡す)。それも無ければ等価 (5枚 = 20円)。
    """
    for m in (mai_per_100yen, default_mai):
        if valid_exchange(m):
            return 100.0 / m
    return LOAN_YEN_PER_COIN


def p_high(post: list[float], spec: Spec) -> float:
    return sum(post[spec.high_from():])


def judge(bb: int, rb: int, games: int, spec: Spec, prior: list[float] | None,
          win_yen_per_coin: float = LOAN_YEN_PER_COIN) -> dict:
    """1台分の判別結果 (画面用).

    prior が None (機種の実測配分が無い) のときは、設定ごとの確率だけを一様の前提で返し、
    p_high / 期待時給は None にする。一様の前提ではそれらが甘く出るため。
    """
    calibrated = prior is not None
    post = posterior(bb, rb, games, spec, prior)
    best = max(range(spec.n), key=lambda i: post[i])
    out = {"post": post, "best": spec.settings[best], "calibrated": calibrated,
           "combined": (games / (bb + rb)) if bb + rb else None,
           "p_high": None, "wari": None, "hourly": None, "hourly_lo": None, "hourly_hi": None}
    if calibrated:
        lo, hi = wari_interval(post, spec)
        out.update({
            "p_high": p_high(post, spec),
            "wari": expected_wari(post, spec),
            "hourly": expected_hourly(post, spec, win_yen_per_coin),
            "hourly_lo": hourly_yen(lo, win_yen_per_coin),
            "hourly_hi": hourly_yen(hi, win_yen_per_coin),
        })
    return out


# ---------- 範囲とラベル (案A、2026-09-15) ----------
# 機種全体の高設定の割合は数日単位で大きく動く (5回目のレビュー: 3分割の範囲は区切り日を変えると当たらず、
# 甘い側にも外れた)。利用は「今日1日」なので、**日ごとに推定した機種の配分**それぞれで計算し、
# その 10〜90 パーセンタイルを範囲にする (build_machine_setting_mix の part 1..N = 日ごと)。
# 当たり率・甘い側への外れ・ラベルの正しさは u1 第6版で機種ごとに検証する。
#
# ラベルは範囲の下限・上限から機械的に決める。言葉は強くてよいが、数字の範囲と矛盾させない
# (memory: feedback-honest-numbers-bold-words)。
LABEL_HIGH = "🔥 高設定の可能性大"     # 設定4以上の下限 (10パーセンタイル) ≥ 50% = 9割の日の配分で 50% 以上
LABEL_LOW = "🧊 低設定濃厚"             # 設定1〜3の下限 ≥ 90% (= 設定4以上の上限 ≤ 10%)
LABEL_MAYBE = "👀 高設定の目あり"       # 設定4以上の上限 (90パーセンタイル) ≥ 30%
LABEL_NEUTRAL = "😐 様子見"
RANGE_Q = (0.10, 0.90)
# 範囲に使う日ごとの配分の日数。u1 第6版で検証した学習の日数が 9〜21 日だったので、その範囲に収める
# (6回目のレビュー: 上限が無いと古いイベント日や季節のずれが範囲に残り続け、甘い側へ外れうる)。
# 日数をこの外に広げる運用にするときは u1 を回し直すこと。本番の build_machine_setting_mix もこの値を使う
MIN_DAYS_FOR_RANGE = 9
MAX_DAYS_FOR_RANGE = 21
# 画面の「この数字の見かた」に出す検証結果 (logs/u1_unit_judge_prior_v6_20260915_101615.json)。
#   (過去の日だけで作った範囲に、その後の日の「その日の実測配分で計算し直した設定4以上」が入った台日の割合 %,
#    それが範囲より悪かった (画面が甘く出た) 割合 %, 範囲に入った日数, 採点した日数)
VALIDATION_SUMMARY = {
    "アイムジャグラーEX": (85.1, 2.9, 10, 11), "ゴーゴージャグラー3": (91.8, 0.1, 10, 11),
    "ファンキージャグラー2": (96.4, 3.5, 11, 11), "マイジャグラーV": (87.5, 6.3, 10, 11),
    "ミスタージャグラー": (81.7, 0.0, 6, 7),
}


def label_for(p_high_min: float, p_high_max: float, allow_high: bool = True, allow_low: bool = True) -> str:
    """範囲からラベルを決める。allow_* が False のラベルは付けない (その機種で正しさを確かめていない)."""
    if p_high_min >= 0.5 and allow_high:
        return LABEL_HIGH
    if 1.0 - p_high_max >= 0.9 and allow_low:
        return LABEL_LOW
    if p_high_max >= 0.3:
        return LABEL_MAYBE
    return LABEL_NEUTRAL


def quantile(values: list[float], q: float) -> float:
    """線形補間の分位点 (numpy.quantile の既定 method='linear' と同じ。検証とそろえるため)."""
    v = sorted(values)
    if not v:
        raise ValueError("値がありません")
    pos = (len(v) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def judge_range(bb: int, rb: int, games: int, spec: Spec, priors: list[list[float]],
                win_yen_per_coin: float = LOAN_YEN_PER_COIN, q: tuple[float, float] = RANGE_Q,
                allow_high: bool = True, allow_low: bool = True) -> dict:
    """日ごとの配分それぞれで判別し、設定4以上と期待時給の分位点の範囲・ラベルを返す (画面用)."""
    if not priors:
        raise ValueError("配分がありません")
    rs = [judge(bb, rb, games, spec, pr, win_yen_per_coin) for pr in priors]
    ph = [r["p_high"] for r in rs]
    hr = [r["hourly"] for r in rs]
    lo, hi = quantile(ph, q[0]), quantile(ph, q[1])
    return {"p_high_min": lo, "p_high_max": hi,
            "hourly_min": quantile(hr, q[0]), "hourly_max": quantile(hr, q[1]),
            "label": label_for(lo, hi, allow_high, allow_low),
            "combined": rs[0]["combined"], "posts": [r["post"] for r in rs]}


def mean_posterior(posts: list[list[float]]) -> list[float]:
    """複数台の事後確率の平均 (「入力した台の設定の見込み」)."""
    if not posts:
        raise ValueError("台がありません")
    n = len(posts[0])
    return [sum(p[i] for p in posts) / len(posts) for i in range(n)]

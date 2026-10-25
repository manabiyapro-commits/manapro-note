#!/usr/bin/env python3
"""note記事アーカイブ & 執筆支援ツール（標準ライブラリのみ）

使い方（note-writing/ で実行）:
  python3 tools/note_tool.py count    --brand manabiya-project
  python3 tools/note_tool.py fetch    --brand manabiya-project --limit 3
  python3 tools/note_tool.py index    --brand manabiya-project
  python3 tools/note_tool.py analyze  --brand manabiya-project
  python3 tools/note_tool.py overlap  --brand manabiya-project "新しい記事のタイトルやキーワード"

方針:
  - 公開ページだけを取得する。ログイン・有料部分の取得はしない。
  - robots.txt を確認し、リクエスト間に待機時間（既定 5 秒）を入れる。
  - 取得に失敗した記事は推測で補完せず、理由とURLを失敗一覧に記録する。
"""

import argparse
import csv
import datetime as dt
import email.utils
import html
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "manabiya-note-archiver/1.0 (archive of the account owner's own public articles)"
BODY_CLASS = "note-common-styles__textnote-body"
PAYWALL_MARKERS = ("paywall", "この続きをみるには", "続きをみるには")


# ---------------------------------------------------------------- 共通

def brand_dir(brand):
    d = ROOT / "brands" / brand
    if not (d / "config.json").exists():
        sys.exit(f"ブランド設定が見つかりません: {d / 'config.json'}")
    return d


def load_config(brand):
    return json.loads((brand_dir(brand) / "config.json").read_text(encoding="utf-8"))


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.read().decode(res.headers.get_content_charset() or "utf-8", "replace")


class Polite:
    """robots.txt の確認とアクセス間隔の制御"""

    def __init__(self, delay):
        self.delay = delay
        self.last = 0.0
        self.robots = urllib.robotparser.RobotFileParser()
        self.robots.set_url("https://note.com/robots.txt")
        try:
            self.robots.parse(http_get("https://note.com/robots.txt").splitlines())
        except Exception as e:
            sys.exit(f"robots.txt を取得できないため中止します: {e}")
        self.last = time.monotonic()

    def get(self, url):
        if not self.robots.can_fetch(USER_AGENT, url):
            raise PermissionError("robots.txt で許可されていないURLです")
        wait = self.delay - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        try:
            return http_get(url)
        finally:
            self.last = time.monotonic()


def article_key(url):
    m = re.search(r"/n/([A-Za-z0-9]+)", url)
    return m.group(1) if m else re.sub(r"\W+", "_", url)[-40:]


def now_iso():
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- RSS

def read_rss(polite, rss_url):
    root = ET.fromstring(polite.get(rss_url))
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate") or ""
        try:
            date = email.utils.parsedate_to_datetime(pub).date().isoformat()
        except Exception:
            date = ""
        items.append({"title": (it.findtext("title") or "").strip(), "url": link, "date": date})
    return items


# ---------------------------------------------------------------- HTML → Markdown

class BodyExtractor(HTMLParser):
    """本文コンテナ（BODY_CLASS）の中身だけを Markdown に変換する"""

    BLOCKS = {"p", "div", "figure", "figcaption", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0          # 本文コンテナ内の div ネスト深さ（0 = 外）
        self.found = False
        self.out = []
        self.lists = []         # "ul" / "ol" のスタック
        self.ol_count = []
        self.href = None
        self.link_text = []
        self.pre = False
        self.quote = 0
        self.hashtags = []

    def emit(self, s):
        if self.href is not None:
            self.link_text.append(s)
        else:
            self.out.append(s)

    def newline(self, n=1):
        self.out.append("\n" * n)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        href = a.get("href") or ""
        m = re.search(r"/hashtag/([^/?#\"]+)", href)
        if m:
            self.hashtags.append(urllib.parse.unquote(m.group(1)))
        if self.depth == 0:
            if tag == "div" and BODY_CLASS in (a.get("class") or ""):
                self.depth, self.found = 1, True
            return
        if tag == "div":
            self.depth += 1
        if tag in ("h1", "h2", "h3", "h4"):
            self.newline(2)
            self.emit("#" * max(2, int(tag[1])) + " ")
        elif tag in self.BLOCKS:
            self.newline(2)
            if self.quote:
                self.emit("> ")
        elif tag == "br":
            self.newline()
            if self.quote:
                self.emit("> ")
        elif tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")
        elif tag == "s":
            self.emit("~~")
        elif tag == "a":
            self.href, self.link_text = href, []
        elif tag == "img":
            src = a.get("data-src") or a.get("src") or ""
            if src:
                self.newline(2)
                self.emit(f"![{a.get('alt', '')}]({src})")
        elif tag in ("ul", "ol"):
            self.lists.append(tag)
            self.ol_count.append(0)
            self.newline()
        elif tag == "li":
            self.newline()
            indent = "  " * (len(self.lists) - 1)
            if self.lists and self.lists[-1] == "ol":
                self.ol_count[-1] += 1
                self.emit(f"{indent}{self.ol_count[-1]}. ")
            else:
                self.emit(f"{indent}- ")
        elif tag == "blockquote":
            self.quote += 1
            self.newline(2)
        elif tag == "pre":
            self.pre = True
            self.newline(2)
            self.emit("```\n")
        elif tag == "hr":
            self.newline(2)
            self.emit("---")

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                return
        if tag in ("strong", "b"):
            self.emit("**")
        elif tag in ("em", "i"):
            self.emit("*")
        elif tag == "s":
            self.emit("~~")
        elif tag == "a" and self.href is not None:
            text, href = "".join(self.link_text).strip(), self.href
            self.href = None
            self.emit(f"[{text}]({href})" if href and text else text)
        elif tag in ("ul", "ol") and self.lists:
            self.lists.pop()
            self.ol_count.pop()
            self.newline()
        elif tag == "blockquote" and self.quote:
            self.quote -= 1
            self.newline(2)
        elif tag == "pre":
            self.pre = False
            self.emit("\n```")
            self.newline(2)
        elif tag in ("h1", "h2", "h3", "h4") or tag in self.BLOCKS:
            self.newline(2)

    def handle_data(self, data):
        if self.depth == 0:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
        self.emit(data)

    def markdown(self):
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n(> )?\n(> \n)+", "\n\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def meta_content(page, prop):
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', page)
    return html.unescape(m.group(1)) if m else ""


def parse_article(page):
    ex = BodyExtractor()
    ex.feed(page)
    if not ex.found:
        return None
    body = ex.markdown()
    if not body.strip():
        return None
    # ページに埋め込まれた記事データ（エスケープ済みJSON）の canRead / price で有料かどうかを判定する。
    # "paywall" などの文字列は無料記事のページにも含まれるため使わない。
    can_read = re.search(r'canRead\\*"\s*:\s*(true|false)', page)
    price = re.search(r'price\\*"\s*:\s*(\d+)', page)
    if can_read:
        partial = can_read.group(1) == "false"
    else:
        partial = any(m in page for m in PAYWALL_MARKERS[1:])
    return {
        "body": body,
        "hashtags": list(dict.fromkeys(ex.hashtags)),
        "partial": partial,
        "price": int(price.group(1)) if price else None,
        "description": meta_content(page, "og:description"),
    }


# ---------------------------------------------------------------- 保存

def yaml_str(s):
    return json.dumps(s, ensure_ascii=False)


def save_article(bdir, cfg, item, art):
    key = article_key(item["url"])
    path = bdir / "articles" / f"{item['date'] or 'nodate'}_{key}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f"title: {yaml_str(item['title'])}",
        f"url: {item['url']}",
        f"published: {item['date']}",
        f"brand: {yaml_str(cfg['brand'])}",
        f"hashtags: {yaml_str(art['hashtags'])}",
        f"partial: {'true' if art['partial'] else 'false'}",
        f"price: {art['price'] if art.get('price') is not None else ''}",
        f"fetched_at: {now_iso()}",
        "---",
        "",
        f"# {item['title']}",
        "",
    ]
    if art["partial"]:
        fm += ["> ※有料記事のため、無料公開部分のみ保存しています。", ""]
    path.write_text("\n".join(fm) + art["body"], encoding="utf-8")
    return path


def read_front_matter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(": ")
        v = v.strip()
        if v.startswith(("[", '"')):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        meta[k.strip()] = v
    return meta, m.group(2)


def load_articles(bdir):
    arts = []
    for p in sorted((bdir / "articles").glob("*.md")):
        meta, body = read_front_matter(p)
        meta["path"], meta["body"] = p, body
        arts.append(meta)
    return arts


def load_failures(bdir):
    p = bdir / "failures.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_failures(bdir, failures):
    (bdir / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 取得できなかった記事", "",
             "推測による補完はしていません。原因を解消したら `fetch` で再取得できます。", ""]
    if failures:
        lines += ["| 投稿日 | タイトル | URL | 理由 | 確認日時 |", "|---|---|---|---|---|"]
        for url, f in sorted(failures.items(), key=lambda kv: kv[1].get("date", ""), reverse=True):
            lines.append(f"| {f.get('date', '')} | {f.get('title', '')} | {url} | {f['reason']} | {f['at']} |")
    else:
        lines.append("現在、取得できなかった記事はありません。")
    (bdir / "取得失敗一覧.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- コマンド

def cmd_count(args):
    cfg = load_config(args.brand)
    polite = Polite(args.delay)
    items = read_rss(polite, cfg["rss"])
    print(f"RSSに含まれる記事数: {len(items)}")
    for it in items:
        print(f"  {it['date']}  {it['title']}  {it['url']}")
    print("\n※noteのRSSは最新の一定件数のみを配信している可能性があります。"
          "アカウントの総記事数と一致するかは、noteの画面で確認してください。")


def cmd_fetch(args):
    cfg = load_config(args.brand)
    bdir = brand_dir(args.brand)
    polite = Polite(args.delay)
    items = read_rss(polite, cfg["rss"])
    done = {article_key(a["url"]) for a in load_articles(bdir)}
    failures = load_failures(bdir)
    targets = [it for it in items if args.refresh or article_key(it["url"]) not in done]
    if args.limit:
        targets = targets[: args.limit]
    print(f"RSS: {len(items)}件 / 取得対象: {len(targets)}件（待機 {args.delay} 秒/件）")
    ok = 0
    for it in targets:
        reason = None
        try:
            if not it["url"].startswith("https://note.com/"):
                raise ValueError("note.com 以外のURLのため対象外")
            art = parse_article(polite.get(it["url"]))
            if art is None:
                reason = "本文要素が見つからない（ページ構造の変更、または非公開の可能性）"
            else:
                path = save_article(bdir, cfg, it, art)
                failures.pop(it["url"], None)
                ok += 1
                note = "（有料記事：無料部分のみ）" if art["partial"] else ""
                print(f"  OK  {it['title']} → {path.relative_to(ROOT)}{note}")
        except urllib.error.HTTPError as e:
            reason = f"HTTP {e.code}（{'非公開・削除' if e.code in (403, 404, 410) else 'サーバー応答エラー'}の可能性）"
        except urllib.error.URLError as e:
            reason = f"接続エラー: {e.reason}"
        except PermissionError as e:
            reason = str(e)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
        if reason:
            failures[it["url"]] = {"title": it["title"], "date": it["date"], "reason": reason, "at": now_iso()}
            print(f"  NG  {it['title']}  {it['url']}  理由: {reason}")
            if reason.startswith("HTTP 429"):
                print("アクセス制限を受けたため中止します。時間をおいて再実行してください。")
                break
    write_failures(bdir, failures)
    write_index(bdir, items)
    print(f"\n保存 {ok}件 / 失敗 {len(failures)}件（詳細: {(bdir / '取得失敗一覧.md').relative_to(ROOT)}）")


def write_index(bdir, rss_items=None):
    arts = load_articles(bdir)
    saved = {article_key(a["url"]): a for a in arts}
    rows = {}
    for it in rss_items or []:
        rows[article_key(it["url"])] = {"date": it["date"], "title": it["title"], "url": it["url"]}
    for k, a in saved.items():
        rows[k] = {"date": a.get("published", ""), "title": a.get("title", ""), "url": a.get("url", "")}
    failures = load_failures(bdir)
    out = sorted(rows.items(), key=lambda kv: kv[1]["date"], reverse=True)

    with (bdir / "記事一覧.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["投稿日", "タイトル", "URL", "状態", "ハッシュタグ", "保存ファイル"])
        for k, r in out:
            a = saved.get(k)
            state = ("保存済（無料部分のみ）" if a.get("partial") == "true" else "保存済") if a else \
                ("取得失敗" if r["url"] in failures else "未取得")
            w.writerow([r["date"], r["title"], r["url"], state,
                        " ".join(f"#{t}" for t in (a or {}).get("hashtags", []) or []),
                        a["path"].relative_to(bdir).as_posix() if a else ""])

    lines = ["# 記事一覧", "", f"更新: {now_iso()}　件数: {len(out)}（保存済 {len(saved)}）", "",
             "新しい記事のテーマ重複は `python3 tools/note_tool.py overlap` で確認できます。", "",
             "| 投稿日 | タイトル | 状態 |", "|---|---|---|"]
    for k, r in out:
        a = saved.get(k)
        state = f"[保存済](articles/{a['path'].name})" if a else ("取得失敗" if r["url"] in failures else "未取得")
        lines.append(f"| {r['date']} | [{r['title']}]({r['url']}) | {state} |")
    (bdir / "記事一覧.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_index(args):
    write_index(brand_dir(args.brand))
    print("記事一覧.md / 記事一覧.csv を更新しました。")


# ---------------------------------------------------------------- 分析

def plain_text(md):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^[#>\-\d. ]+", "", t, flags=re.M)
    return t.replace("**", "").replace("~~", "")


def sentence_endings(text):
    sents = [s.strip() for s in re.split(r"(?<=[。！？!?])", text) if s.strip()]
    c = Counter()
    for s in sents:
        s2 = re.sub(r"[」』）)\s。！？!?]+$", "", s)
        if re.search(r"(です|ます|でした|ました|ません|でしょう|ましょう)$", s2):
            c["です・ます"] += 1
        elif re.search(r"(だ|である|だった|た|ない|る)$", s2):
            c["だ・である/常体"] += 1
        else:
            c["体言止め・その他"] += 1
        if re.search(r"[？?]$", s):
            c["疑問文"] += 1
    return c, len(sents)


def cmd_analyze(args):
    bdir = brand_dir(args.brand)
    cfg = load_config(args.brand)
    arts = load_articles(bdir)
    if not arts:
        sys.exit("保存済みの記事がありません。先に fetch を実行してください。")

    lengths, heads, endings, total_sents = [], [], Counter(), 0
    tags, firsts, title_feats, words = Counter(), Counter(), Counter(), Counter()
    per = []
    for a in arts:
        body = a["body"]
        text = plain_text(body)
        n = len(re.sub(r"\s", "", text))
        h = re.findall(r"^(#{2,4}) (.+)$", body, re.M)
        e, ns = sentence_endings(text)
        endings.update(e)
        total_sents += ns
        lengths.append(n)
        heads.append(len(h))
        tags.update(a.get("hashtags") or [])
        for p in ("私たち", "わたし", "私", "僕", "ぼく", "筆者", "当プロジェクト"):
            firsts[p] += text.count(p)
        t = a.get("title", "")
        for name, pat in [("【】を使う", r"【.+?】"), ("数字を含む", r"\d"), ("？を含む", r"[？?]"),
                          ("！を含む", r"[！!]"), ("「」を含む", r"「.+?」")]:
            if re.search(pat, t):
                title_feats[name] += 1
        words.update(re.findall(r"[一-龥ァ-ヶー]{2,}", text))
        per.append((a, n, h))

    dates = sorted(a.get("published", "") for a in arts if a.get("published"))
    gaps = []
    for d1, d2 in zip(dates, dates[1:]):
        gaps.append((dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days)

    def pct(x, total):
        return f"{x / total * 100:.0f}%" if total else "-"

    L = [f"# 自動集計レポート（{cfg['brand']}）", "",
         f"生成: {now_iso()}　対象: 保存済み {len(arts)} 記事"
         + (f"（うち無料部分のみ {sum(a.get('partial') == 'true' for a in arts)} 記事）" if any(a.get('partial') == 'true' for a in arts) else ""),
         "", "このレポートは数値の集計のみです。解釈は「執筆スタイルガイド.md」にまとめます。", "",
         "## 投稿", f"- 期間: {dates[0] if dates else '-'} 〜 {dates[-1] if dates else '-'}",
         f"- 平均投稿間隔: {statistics.mean(gaps):.1f} 日" if gaps else "- 平均投稿間隔: -", "",
         "## 分量",
         f"- 本文文字数: 平均 {statistics.mean(lengths):.0f} / 中央値 {statistics.median(lengths):.0f} / 最小 {min(lengths)} / 最大 {max(lengths)}",
         f"- 見出し数: 平均 {statistics.mean(heads):.1f}", "",
         "## 文末（全 %d 文）" % total_sents]
    for k in ("です・ます", "だ・である/常体", "体言止め・その他", "疑問文"):
        L.append(f"- {k}: {endings[k]}（{pct(endings[k], total_sents)}）")
    L += ["", "## 一人称・自称（出現回数）"]
    L += [f"- {k}: {v}" for k, v in firsts.most_common() if v] or ["- なし"]
    L += ["", "## タイトルの特徴（該当記事数）"]
    L += [f"- {k}: {v} / {len(arts)}" for k, v in title_feats.most_common()] or ["- 特になし"]
    L += ["", "## ハッシュタグ（使用記事数）"]
    L += [f"- #{k}: {v}" for k, v in tags.most_common(30)] or ["- 取得できたハッシュタグなし"]
    L += ["", "## 頻出語（漢字・カタカナ2文字以上、上位40）", "",
          "、".join(f"{w}({c})" for w, c in words.most_common(40)), "",
          "## 記事別の構成", ""]
    for a, n, h in per:
        L.append(f"### {a.get('published', '')} {a.get('title', '')}")
        L.append(f"- 文字数 {n} / 見出し {len(h)} / ハッシュタグ: {' '.join('#' + t for t in a.get('hashtags') or []) or 'なし'}")
        L += [f"  {'  ' * (len(m) - 2)}- {t}" for m, t in h]
        L.append("")
    out = bdir / "分析" / "自動集計レポート.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{out.relative_to(ROOT)} を作成しました（{len(arts)}記事）。")


# ---------------------------------------------------------------- 重複チェック

def bigrams(s):
    s = re.sub(r"[\s\W_]+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def cmd_overlap(args):
    bdir = brand_dir(args.brand)
    q = bigrams(args.query)
    if not q:
        sys.exit("タイトルまたはキーワードを指定してください。")
    rows = []
    for a in load_articles(bdir):
        heads = " ".join(re.findall(r"^#{2,4} (.+)$", a["body"], re.M))
        title_score = len(q & bigrams(a.get("title", ""))) / len(q)
        doc = bigrams(a.get("title", "") + heads + " ".join(a.get("hashtags") or []))
        content_score = len(q & doc) / len(q)
        rows.append((max(title_score, content_score * 0.8), title_score, a))
    # 未取得（一覧のみ）の記事もタイトルで照合する
    csv_path = bdir / "記事一覧.csv"
    saved_urls = {a.get("url") for _, _, a in rows}
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r["URL"] not in saved_urls:
                    s = len(q & bigrams(r["タイトル"])) / len(q)
                    rows.append((s, s, {"title": r["タイトル"], "url": r["URL"], "published": r["投稿日"]}))
    rows.sort(key=lambda r: r[0], reverse=True)
    print(f"照合: 「{args.query}」\n")
    for score, ts, a in rows[: args.top]:
        flag = "★重複の可能性大" if score >= 0.5 else ("△要確認" if score >= 0.3 else "")
        print(f"{score:.2f} {flag:<8} {a.get('published', '')} {a.get('title', '')}  {a.get('url', '')}")
    if not rows:
        print("比較対象の記事がありません。")


def main():
    ap = argparse.ArgumentParser(description="note記事アーカイブ & 執筆支援ツール")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("count", "fetch", "index", "analyze", "overlap"):
        p = sub.add_parser(name)
        p.add_argument("--brand", required=True, help="brands/ 以下のフォルダ名")
        if name in ("count", "fetch"):
            p.add_argument("--delay", type=float, default=5.0, help="リクエスト間の待機秒数（既定 5、最低 3）")
        if name == "fetch":
            p.add_argument("--limit", type=int, default=0, help="取得する最大件数（0 = 全件）")
            p.add_argument("--refresh", action="store_true", help="保存済みの記事も取得し直す")
        if name == "overlap":
            p.add_argument("query")
            p.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    if getattr(args, "delay", 5) < 3:
        args.delay = 3.0
    {"count": cmd_count, "fetch": cmd_fetch, "index": cmd_index,
     "analyze": cmd_analyze, "overlap": cmd_overlap}[args.cmd](args)


if __name__ == "__main__":
    main()

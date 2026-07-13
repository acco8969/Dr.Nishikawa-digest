"""
週次 医学論文・記事 候補収集スクリプト（無料版）
"""

import os
import re
import time
import smtplib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.header import Header

import requests
import feedparser

# ============================================================
# CONFIG（ここを編集してください）
# ============================================================

KEYWORD_GROUPS = [
    ["ocular blood flow", "flicker"],
    ["Ninjurin1", "retina"],
]

RSS_FEEDS = [
    # "https://example-medical-news.com/rss",
]

DAYS_BACK = 7
PUBMED_MAX_RESULTS = 20

# ============================================================
# PubMed 検索
# ============================================================

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_pubmed(terms: list, days_back: int, max_results: int):
    query = " AND ".join(f'"{t}"' for t in terms)
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "sort": "date",
        "datetype": "pdat",
        "reldate": days_back,
        "retmode": "json",
    }
    resp = requests.get(f"{EUTILS_BASE}/esearch.fcgi", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("esearchresult", {}).get("idlist", [])


def fetch_pubmed_details(pmids: list):
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    resp = requests.get(f"{EUTILS_BASE}/efetch.fcgi", params=params, timeout=30)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    articles = []
    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//PMID")
        title_el = art.find(".//ArticleTitle")
        abstract_parts = art.findall(".//AbstractText")

        pmid = pmid_el.text if pmid_el is not None else None
        title = "".join(title_el.itertext()) if title_el is not None else "(タイトル不明)"
        abstract = " ".join("".join(a.itertext()) for a in abstract_parts) if abstract_parts else ""

        if pmid is None:
            continue

        articles.append(
            {
                "source": "PubMed",
                "title": title.strip(),
                "summary": abstract.strip(),
                "link": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return articles


def collect_pubmed_articles():
    all_articles = {}
    for group in KEYWORD_GROUPS:
        try:
            pmids = search_pubmed(group, DAYS_BACK, PUBMED_MAX_RESULTS)
            time.sleep(0.4)
            details = fetch_pubmed_details(pmids)
            for a in details:
                all_articles[a["link"]] = a
        except Exception as e:
            print(f"[WARN] PubMed検索でエラー（group={group}）: {e}")
    return list(all_articles.values())


# ============================================================
# RSS 収集
# ============================================================

def collect_rss_articles():
    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    results = {}

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            source_name = feed.feed.get("title", feed_url)

            for entry in feed.entries:
                published = _parse_entry_date(entry)
                if published is not None and published < cutoff:
                    continue

                title = entry.get("title", "")
                summary = re.sub("<[^<]+?>", "", entry.get("summary", ""))
                text_blob = f"{title} {summary}".lower()

                matched = any(
                    all(term.lower() in text_blob for term in group)
                    for group in KEYWORD_GROUPS
                )
                if not matched:
                    continue

                link = entry.get("link", "")
                if not link:
                    continue

                results[link] = {
                    "source": source_name,
                    "title": title.strip(),
                    "summary": summary.strip(),
                    "link": link,
                }
        except Exception as e:
            print(f"[WARN] RSS取得でエラー（{feed_url}）: {e}")

    return list(results.values())


def _parse_entry_date(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


# ============================================================
# メール作成・送信
# ============================================================

def build_email_body(items: list):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"■ 週次 医学情報 候補一覧（{today}）",
        "",
        "※このメールはキーワード一致による自動収集の「候補」です。まだ内容の精査はされていません。",
        "  このメールの本文をそのままコピーして claude.ai に貼り付け、",
        "  「本当に関連性が高いものだけ日本語で要約して」と依頼してください。",
        "",
    ]

    if not items:
        lines.append("今週は該当する新着情報がありませんでした。")
    else:
        lines.append(f"候補件数: {len(items)}件")
        lines.append("")
        for i, item in enumerate(items, 1):
            snippet = item["summary"][:300].replace("\n", " ")
            lines.append(f"{i}. [{item['source']}] {item['title']}")
            if snippet:
                lines.append(f"   抄録抜粋: {snippet}")
            lines.append(f"   リンク: {item['link']}")
            lines.append("")

    return "\n".join(lines)


def send_email(subject: str, body: str):
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = [r.strip() for r in os.environ["RECIPIENT_EMAIL"].split(",")]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = gmail_address
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, recipients, msg.as_string())


# ============================================================
# メイン処理
# ============================================================

def main():
    print("PubMedから収集中...")
    pubmed_articles = collect_pubmed_articles()
    print(f"  -> {len(pubmed_articles)}件（重複除去後）")

    print("RSSから収集中...")
    rss_articles = collect_rss_articles()
    print(f"  -> {len(rss_articles)}件（キーワード一致の候補）")

    all_candidates = pubmed_articles + rss_articles
    print(f"合計候補: {len(all_candidates)}件")

    body = build_email_body(all_candidates)
    subject = f"【週次・候補一覧】医学情報ダイジェスト（{datetime.now().strftime('%Y-%m-%d')}）"

    send_email(subject, body)
    print("メール送信完了。")


if __name__ == "__main__":
    main()

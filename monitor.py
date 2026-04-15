#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本
- 抓取指定 X 账号推文（通过 Nitter）
- 从新闻网站首页自动提取文章链接并抓取详情
- 保存到 items.json，按条目保留时间戳
- 生成最近6小时的 index.html 并推送到 gh-pages
"""

import os
import json
import time
import random
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup

# ==================== 配置区 ====================
# 你要监测的 X 账号列表（不带@）
X_ACCOUNTS = [
    "foreignofficepk",
    "mishaqdar50",
    "cmshehbaz",
    "IranAmbPak",
    "paktvglobal",
    "Tasnimnews_Fa",
    "araghchi",
    "irimfa",
    "mb_ghalibaf",
    "AJENews",
    "whitehouse",
    "usembislamabad",
    "CBSNews",
    "JenniferJJacobs",
    "KellieMeyerNews",
    "realdonaldtrump",
    "vp",
    "CGTNEurope"
]

# 新闻网站首页列表（脚本会自动从首页提取文章链接）
NEWS_URLS = [
    "https://www.dawn.com",
    "https://arynews.tv"
]

# Nitter 实例池（2026年4月可用实例）
NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.domain.glass",
    "https://nitter.it"
]

# 输出文件
ITEMS_FILE = "items.json"
HTML_FILE = "index.html"

# ==================== 工具函数 ====================
def get_headers():
    """生成一个看起来更像真实浏览器的请求头"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Cache-Control': 'max-age=0',
    }

def get_healthy_nitter_instance():
    """健康检查：随机选一个实例，测试首页是否200"""
    random.shuffle(NITTER_INSTANCES)
    for inst in NITTER_INSTANCES:
        try:
            r = requests.get(inst, timeout=5, headers=get_headers())
            if r.status_code == 200:
                return inst
        except:
            continue
    return NITTER_INSTANCES[0]  # 全挂了就硬用第一个

def fetch_tweets(username):
    """通过 Nitter 抓取某账号的推文（最多10条）"""
    instance = get_healthy_nitter_instance()
    url = f"{instance}/{username}"
    try:
        r = requests.get(url, headers=get_headers(), timeout=10)
        if r.status_code != 200:
            print(f"  Nitter 返回 {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        tweets = []
        # 查找推文内容（根据 Nitter 的 HTML 结构）
        for tweet_div in soup.select(".tweet-content"):
            text = tweet_div.get_text(strip=True)
            if not text:
                continue
            # 找时间链接
            time_link = tweet_div.find_previous("a", class_="tweet-date")
            if time_link:
                time_str = time_link.get("title") or time_link.text
                tweet_url = time_link.get("href", "")
            else:
                time_str = datetime.now(timezone.utc).isoformat()
                tweet_url = ""
            tweet_id = tweet_url.split("/")[-1] if tweet_url else str(hash(text))
            tweets.append({
                "id": f"tweet_{username}_{tweet_id}",
                "type": "tweet",
                "username": username,
                "text": text,
                "url": instance + tweet_url if tweet_url else "",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "original_time": time_str
            })
            if len(tweets) >= 10:  # 每个账号最多10条，避免过多
                break
        print(f"  从 @{username} 抓取到 {len(tweets)} 条推文")
        return tweets
    except Exception as e:
        print(f"  抓取 @{username} 失败: {e}")
        return []

def load_items():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

def extract_article_links(homepage_url):
    """从新闻首页提取所有文章链接（返回绝对URL列表）"""
    try:
        r = requests.get(homepage_url, headers=get_headers(), timeout=10)
        if r.status_code != 200:
            print(f"  首页请求失败 {homepage_url}，状态码 {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            # 匹配常见文章路径模式
            if ('/news/' in href or '/story/' in href or '/article/' in href 
                or '/2026/' in href or href.startswith('/politics/') 
                or href.startswith('/business/') or href.startswith('/world/')):
                if href.startswith('/'):
                    full_url = homepage_url.rstrip('/') + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    continue
                # 排除明显不是文章页的链接
                if any(x in full_url for x in ['/video', '/live', '/gallery', '/tag/', '/category/', '/author/']):
                    continue
                links.add(full_url)
        
        # 限制最多前15个最新文章
        result = list(links)[:15]
        print(f"  从 {homepage_url} 提取到 {len(result)} 个文章链接")
        return result
    except Exception as e:
        print(f"  提取文章链接失败 {homepage_url}: {e}")
        return []

def fetch_article_detail(article_url):
    """抓取单篇文章详情（标题、发布时间）"""
    try:
        r = requests.get(article_url, headers=get_headers(), timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 标题
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"
        
        # 发布时间
        pub_time = None
        meta_time = soup.find("meta", {"property": "article:published_time"})
        if meta_time and meta_time.get("content"):
            pub_time = meta_time["content"]
        else:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                pub_time = time_tag["datetime"]
            else:
                pub_time = datetime.now(timezone.utc).isoformat()
        
        url_hash = hashlib.md5(article_url.encode()).hexdigest()[:12]
        
        return {
            "id": f"article_{url_hash}",
            "type": "article",
            "source": article_url.split("/")[2],
            "title": title,
            "url": article_url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_time": pub_time
        }
    except Exception as e:
        print(f"  抓取文章详情失败 {article_url}: {e}")
        return None

def filter_recent_items(items, hours=6):
    """保留最近 hours 小时内的条目（基于 original_time 或 timestamp）"""
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=hours)
    recent = []
    for item in items:
        ts_str = item.get("original_time") or item.get("timestamp")
        if not ts_str:
            continue
        if ts_str.endswith("Z"):
            ts_str = ts_str.replace("Z", "+00:00")
        try:
            item_time = datetime.fromisoformat(ts_str)
        except:
            continue
        if item_time >= cutoff:
            recent.append(item)
    return recent

def generate_html(recent_items):
    """生成 index.html"""
    tweets = [i for i in recent_items if i["type"] == "tweet"]
    articles = [i for i in recent_items if i["type"] == "article"]
    tweets.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    articles.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>美伊谈判监测 · 最近6小时</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #2c3e50; border-left: 5px solid #e74c3c; padding-left: 15px; }}
        .tweet {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #1da1f2; }}
        .tweet .username {{ font-weight: bold; color: #1da1f2; }}
        .tweet .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .article {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #27ae60; }}
        .article .source {{ font-weight: bold; color: #27ae60; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }}
        hr {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>📡 美伊谈判实时监测</h1>
    <p>🕒 更新时间：{update_time} (UTC) | 显示最近6小时内数据 | 每分钟自动刷新</p>
    <h2>🐦 X 推文 ({tweet_count})</h2>
    {tweets_html}
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：Nitter + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过6小时的内容</p>
    </div>
</body>
</html>"""
    
    tweets_html = ""
    for t in tweets:
        tweets_html += f'''
        <div class="tweet">
            <div class="username">@{t.get("username", "")}</div>
            <div>{t.get("text", "")}</div>
            <div class="time">🕒 {t.get("original_time", t.get("timestamp", ""))}</div>
            <div><a href="{t.get("url", "#")}" target="_blank">查看原文</a></div>
        </div>
        '''
    if not tweets:
        tweets_html = "<p>暂无最近6小时的新推文。</p>"
    
    articles_html = ""
    for a in articles:
        articles_html += f'''
        <div class="article">
            <div class="source">{a.get("source", "")}</div>
            <div><a href="{a.get("url", "#")}" target="_blank">{a.get("title", "")}</a></div>
            <div class="time">🕒 {a.get("original_time", a.get("timestamp", ""))}</div>
        </div>
        '''
    if not articles:
        articles_html = "<p>暂无最近6小时的新文章。</p>"
    
    update_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    html = html_template.format(
        update_time=update_time,
        tweet_count=len(tweets),
        article_count=len(articles),
        tweets_html=tweets_html,
        articles_html=articles_html
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

def main():
    print(f"{datetime.now()} 开始抓取...")
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    # 抓取推文
    for username in X_ACCOUNTS:
        print(f"抓取 @{username} ...")
        tweets = fetch_tweets(username)
        for tw in tweets:
            if tw["id"] not in existing_ids:
                new_items.append(tw)
                existing_ids.add(tw["id"])
        time.sleep(random.uniform(2, 5))  # 随机延迟2-5秒，更像人类
    
    # 抓取新闻文章（从首页提取链接）
    for homepage in NEWS_URLS:
        print(f"从首页提取文章链接: {homepage}")
        article_links = extract_article_links(homepage)
        for article_url in article_links:
            print(f"  抓取文章: {article_url}")
            article = fetch_article_detail(article_url)
            if article and article["id"] not in existing_ids:
                new_items.append(article)
                existing_ids.add(article["id"])
            time.sleep(random.uniform(2, 4))  # 随机延迟2-4秒
    
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"新增 {len(new_items)} 条内容")
    else:
        print("无新内容")
    
    # 过滤最近6小时并生成 HTML
    recent = filter_recent_items(all_items, hours=6)
    generate_html(recent)
    print(f"已生成 {HTML_FILE}，包含 {len(recent)} 条近期内容")
    print("完成")

if __name__ == "__main__":
    main()

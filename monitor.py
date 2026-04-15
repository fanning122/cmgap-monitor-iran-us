#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本
- 抓取指定 X 账号推文（通过 Nitter）
- 抓取指定新闻网站文章
- 保存到 items.json，按条目保留时间戳
- 生成最近6小时的 index.html 并推送到 gh-pages
"""

import os
import json
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from typing import List, Dict, Any

# ==================== 配置区 ====================
# 你要监测的 X 账号列表（不带@）
X_ACCOUNTS = [    
    "foreignofficepk",
    "mishaqdar50",
    "cmshehbaz",
    "IranAmbPak",
    "paktvglobal",
    # "geonews urdu" 需要确认正确用户名
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

# 新闻网站 URL 清单（示例，你需要替换成真实的 Dawn / ARY News 监测链接）
NEWS_URLS = [
    "https://www.dawn.com",
    "https://arynews.tv"
]

# Nitter 实例池（建议用多个，避免被限制）
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.poast.org",
    "https://nitter.privacydev.net"
]

# 用户代理轮换池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
]

# 输出文件
ITEMS_FILE = "items.json"
HTML_FILE = "index.html"

# ==================== 工具函数 ====================
def get_random_ua():
    return random.choice(USER_AGENTS)

def get_healthy_nitter_instance():
    """简单健康检查：随机选一个实例，测试首页是否200"""
    random.shuffle(NITTER_INSTANCES)
    for inst in NITTER_INSTANCES:
        try:
            r = requests.get(inst, timeout=5, headers={"User-Agent": get_random_ua()})
            if r.status_code == 200:
                return inst
        except:
            continue
    return NITTER_INSTANCES[0]  # 全挂了就硬用第一个

def fetch_tweets(username, since_hours=6):
    """通过 Nitter 抓取某账号最近 since_hours 小时的推文"""
    instance = get_healthy_nitter_instance()
    url = f"{instance}/{username}"
    headers = {"User-Agent": get_random_ua()}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        tweets = []
        # 简化版解析：每个推文在 div.tweet-content 附近
        for tweet_div in soup.select(".tweet-content"):
            text = tweet_div.get_text(strip=True)
            # 尝试找时间链接
            time_link = tweet_div.find_previous("a", class_="tweet-date")
            if time_link:
                time_str = time_link.get("title") or time_link.text
            else:
                time_str = datetime.now(timezone.utc).isoformat()
            # 推文唯一ID（从链接提取）
            tweet_url = time_link.get("href") if time_link else ""
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
        return tweets
    except Exception as e:
        print(f"抓取 {username} 失败: {e}")
        return []

def fetch_article(url):
    """抓取单篇新闻文章（支持 Dawn 和 ARY News）"""
    headers = {"User-Agent": get_random_ua()}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"  请求失败，状态码 {r.status_code}")
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        
        # 提取标题
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"
        
        # 提取发布时间（针对 Dawn）
        pub_time = None
        # Dawn 使用 <meta property="article:published_time">
        meta_time = soup.find("meta", {"property": "article:published_time"})
        if meta_time and meta_time.get("content"):
            pub_time = meta_time["content"]
        else:
            # 尝试找 time 标签
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                pub_time = time_tag["datetime"]
            else:
                pub_time = datetime.now(timezone.utc).isoformat()
        
        return {
            "id": f"article_{hash(url)}",
            "type": "article",
            "source": url.split("/")[2],
            "title": title,
            "url": url,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "original_time": pub_time
        }
    except Exception as e:
        print(f"  抓取文章失败 {url}: {e}")
        return None

def extract_article_links(homepage_url):
    """从新闻首页提取所有文章链接（返回绝对URL列表）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    }
    try:
        r = requests.get(homepage_url, headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"  首页请求失败 {homepage_url}，状态码 {r.status_code}")
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        
        links = set()
        # 查找所有 <a> 标签，href 包含 /news/ 或 /story/ 等常见文章路径模式
        for a in soup.find_all('a', href=True):
            href = a['href']
            # 过滤：通常文章链接包含 /news/ 或 /story/ 或 /article/，且长度大于一定值
            if '/news/' in href or '/story/' in href or '/article/' in href or '/2026/' in href:
                # 转换为绝对URL
                if href.startswith('/'):
                    full_url = homepage_url.rstrip('/') + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    continue
                # 排除首页、分类页、标签页等（可选）
                if 'category' not in full_url and 'tag' not in full_url:
                    links.add(full_url)
        
        # 限制最多抓取前10个最新文章（避免太多）
        return list(links)[:10]
    except Exception as e:
        print(f"  提取文章链接失败 {homepage_url}: {e}")
        return []

def fetch_article_detail(article_url):
    """抓取单篇文章详情（标题、发布时间）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    }
    try:
        r = requests.get(article_url, headers=headers, timeout=10)
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
        
        # 生成唯一ID
        import hashlib
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

def load_items():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

def filter_recent_items(items, hours=6):
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=hours)
    recent = []
    for item in items:
        # 优先使用 original_time（文章/推文的真实发布时间），如果没有则回退到 timestamp（抓取时间）
        ts_str = item.get("original_time") or item.get("timestamp")
        if not ts_str:
            continue
        # 处理可能的 Z 结尾（UTC标识）
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
    """根据最近条目生成 index.html"""
    tweets = [i for i in recent_items if i["type"] == "tweet"]
    articles = [i for i in recent_items if i["type"] == "article"]
    # 按时间倒序排序
    tweets.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    articles.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    
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
        time.sleep(random.uniform(1, 3))  # 随机延迟
    
    # 抓取新闻文章
# 抓取新闻文章（从首页提取链接）
for homepage in NEWS_URLS:
    print(f"从首页提取文章链接: {homepage}")
    article_links = extract_article_links(homepage)
    print(f"  找到 {len(article_links)} 个文章链接")
    
    for article_url in article_links:
        print(f"  抓取文章: {article_url}")
        article = fetch_article_detail(article_url)
        if article and article["id"] not in existing_ids:
            new_items.append(article)
            existing_ids.add(article["id"])
        time.sleep(random.uniform(1, 2))  # 避免过快
    
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

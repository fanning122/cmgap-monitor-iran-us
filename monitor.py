#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判监测脚本 (Selenium + snscrape版本)
- 使用 Selenium 抓取新闻网站（模拟真实浏览器，解决403）
- 使用 snscrape 抓取 X 平台推文（无需登录，稳定可靠）
- 保存到 items.json，按条目保留时间戳
- 生成最近6小时的 index.html 并推送到 gh-pages
"""

import os
import json
import time
import random
import hashlib
from datetime import datetime, timezone, timedelta

# Selenium 相关
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# snscrape 相关
import snscrape.modules.twitter as sntwitter

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

# 输出文件
ITEMS_FILE = "items.json"
HTML_FILE = "index.html"

# ==================== Selenium 浏览器配置 ====================
def get_driver():
    """配置并返回一个 Chrome WebDriver 实例"""
    chrome_options = Options()
    
    # 无头模式（在服务器上运行时必须）
    chrome_options.add_argument("--headless=new")
    
    # 基础设置
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 反检测：禁用自动化提示
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    
    # 随机 User-Agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
    
    # 创建驱动
    driver = webdriver.Chrome(options=chrome_options)
    return driver

# ==================== 新闻抓取函数 ====================
def extract_article_links_with_selenium(homepage_url):
    """使用 Selenium 从新闻首页提取文章链接"""
    driver = None
    try:
        driver = get_driver()
        print(f"  正在访问首页: {homepage_url}")
        driver.get(homepage_url)
        
        # 等待页面加载完成
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # 获取页面源代码
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")
        
        links = set()
        # 查找所有 <a> 标签，href 包含常见文章路径模式
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href or len(href) < 10:
                continue
                
            # 匹配常见文章路径模式
            if ('/news/' in href or '/story/' in href or '/article/' in href 
                or '/2026/' in href or href.startswith('/politics/') 
                or href.startswith('/business/') or href.startswith('/world/')):
                
                # 转换为绝对URL
                if href.startswith('/'):
                    full_url = homepage_url.rstrip('/') + href
                elif href.startswith('http'):
                    full_url = href
                else:
                    continue
                    
                # 排除明显不是文章页的链接
                if any(x in full_url for x in ['/video', '/live', '/gallery', '/tag/', '/category/', '/author/']):
                    continue
                    
                # 只保留域名匹配的链接
                if homepage_url in full_url:
                    links.add(full_url)
        
        result = list(links)[:15]
        print(f"  从 {homepage_url} 提取到 {len(result)} 个文章链接")
        return result
        
    except Exception as e:
        print(f"  提取文章链接失败 {homepage_url}: {e}")
        return []
    finally:
        if driver:
            driver.quit()

def fetch_article_detail_with_selenium(article_url):
    """使用 Selenium 抓取单篇文章详情（标题、发布时间）"""
    driver = None
    try:
        driver = get_driver()
        driver.get(article_url)
        
        # 等待文章主体加载
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        page_source = driver.page_source
        soup = BeautifulSoup(page_source, "html.parser")
        
        # 标题
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"
        
        # 发布时间
        pub_time = None
        
        # Dawn 的发布时间
        meta_time = soup.find("meta", {"property": "article:published_time"})
        if meta_time and meta_time.get("content"):
            pub_time = meta_time["content"]
        else:
            # ARY News 的发布时间
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                pub_time = time_tag["datetime"]
            elif time_tag:
                pub_time = time_tag.get_text(strip=True)
            else:
                # 尝试找其他包含时间的元素
                date_meta = soup.find("meta", {"name": "pubdate"}) or soup.find("meta", {"name": "date"})
                if date_meta and date_meta.get("content"):
                    pub_time = date_meta["content"]
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
    finally:
        if driver:
            driver.quit()

# ==================== X 平台推文抓取函数 (使用 snscrape) ====================
def fetch_tweets_with_snscrape(username, max_tweets=5):
    """使用 snscrape 抓取指定用户的推文"""
    tweets = []
    try:
        # 使用 snscrape 搜索该用户的最新推文
        query = f"from:{username}"
        for i, tweet in enumerate(sntwitter.TwitterSearchScraper(query).get_items()):
            if i >= max_tweets:
                break
            
            # 提取推文信息
            tweet_id = str(tweet.id)
            text = tweet.content
            date = tweet.date
            
            # 如果推文过长，截断
            if len(text) > 500:
                text = text[:497] + "..."
            
            tweets.append({
                "id": f"tweet_{username}_{tweet_id}",
                "type": "tweet",
                "username": username,
                "text": text,
                "url": tweet.url,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "original_time": date.isoformat() if date else datetime.now(timezone.utc).isoformat()
            })
        
        print(f"  从 @{username} 抓取到 {len(tweets)} 条推文")
        return tweets
        
    except Exception as e:
        print(f"  抓取 @{username} 失败: {e}")
        return []

# ==================== 数据存储与过滤 ====================
def load_items():
    if os.path.exists(ITEMS_FILE):
        with open(ITEMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_items(items):
    with open(ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

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

# ==================== HTML 生成函数 ====================
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
        <p>数据来源：X平台 + Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过6小时的内容</p>
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

# ==================== 主函数 ====================
def main():
    print(f"{datetime.now()} 开始抓取...")
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    # 1. 抓取推文（使用 snscrape）
    print("\n--- 抓取 X 平台推文 ---")
    for username in X_ACCOUNTS:
        print(f"抓取 @{username} ...")
        tweets = fetch_tweets_with_snscrape(username)
        for tw in tweets:
            if tw["id"] not in existing_ids:
                new_items.append(tw)
                existing_ids.add(tw["id"])
        time.sleep(random.uniform(1, 2))  # 随机延迟
    
    # 2. 抓取新闻文章（使用 Selenium）
    print("\n--- 抓取新闻文章 ---")
    for homepage in NEWS_URLS:
        print(f"处理首页: {homepage}")
        article_links = extract_article_links_with_selenium(homepage)
        
        for article_url in article_links:
            print(f"  抓取文章: {article_url}")
            article = fetch_article_detail_with_selenium(article_url)
            if article and article["id"] not in existing_ids:
                new_items.append(article)
                existing_ids.add(article["id"])
            time.sleep(random.uniform(2, 4))  # 避免请求过快
    
    # 3. 保存新内容
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"\n✅ 新增 {len(new_items)} 条内容")
    else:
        print("\n📭 无新内容")
    
    # 4. 过滤最近6小时并生成 HTML
    recent = filter_recent_items(all_items, hours=6)
    generate_html(recent)
    print(f"\n✅ 已生成 {HTML_FILE}，包含 {len(recent)} 条近期内容")
    print("完成")

if __name__ == "__main__":
    main()

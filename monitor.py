#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美伊谈判新闻监测脚本（仅抓取新闻网站首页）
- 使用 Selenium 从 Dawn 和 ARY News 首页提取最新文章链接
- 抓取文章标题、发布时间
- 保存到 items.json，基于发布时间过滤最近6小时
- 生成 index.html 并部署到 GitHub Pages
"""

import os
import json
import time
import random
import hashlib
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# ==================== 配置区 ====================
NEWS_URLS = [
    "https://www.dawn.com",
    "https://arynews.tv"
]

ITEMS_FILE = "items.json"
HTML_FILE = "index.html"

# ==================== Selenium 浏览器配置 ====================
def get_driver():
    """配置 Chrome 无头浏览器"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    
    # 随机 User-Agent
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
    
    return webdriver.Chrome(options=chrome_options)

# ==================== 文章链接提取（从首页） ====================
def extract_article_links(homepage_url):
    """从首页提取文章链接（绝对URL）"""
    driver = None
    try:
        driver = get_driver()
        print(f"  正在访问首页: {homepage_url}")
        driver.get(homepage_url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        links = set()
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href or len(href) < 10:
                continue
            
            # 匹配文章路径模式（根据 Dawn 和 ARY News 结构调整）
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
                
                if homepage_url.split('/')[2] in full_url:  # 确保同域名
                    links.add(full_url)
        
        result = list(links)[:15]  # 最多取15个最新链接
        print(f"  从 {homepage_url} 提取到 {len(result)} 个文章链接")
        return result
    except Exception as e:
        print(f"  提取链接失败 {homepage_url}: {e}")
        return []
    finally:
        if driver:
            driver.quit()

# ==================== 文章详情抓取 ====================
def fetch_article_detail(article_url):
    """抓取单篇文章的标题和发布时间"""
    driver = None
    try:
        driver = get_driver()
        driver.get(article_url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # 标题
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else "无标题"
        
        # 发布时间（优先 article:published_time）
        pub_time = None
        meta_time = soup.find("meta", {"property": "article:published_time"})
        if meta_time and meta_time.get("content"):
            pub_time = meta_time["content"]
        else:
            time_tag = soup.find("time")
            if time_tag and time_tag.get("datetime"):
                pub_time = time_tag["datetime"]
            elif time_tag:
                pub_time = time_tag.get_text(strip=True)
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
        print(f"  抓取详情失败 {article_url}: {e}")
        return None
    finally:
        if driver:
            driver.quit()

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

# ==================== 生成 HTML ====================
def generate_html(recent_items):
    tweets = []  # 无推文
    articles = [i for i in recent_items if i["type"] == "article"]
    articles.sort(key=lambda x: x.get("original_time", x.get("timestamp", "")), reverse=True)
    
    html_template = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>美伊谈判新闻监测 · 最近6小时</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #2c3e50; border-left: 5px solid #e74c3c; padding-left: 15px; }}
        .article {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #27ae60; }}
        .article .source {{ font-weight: bold; color: #27ae60; }}
        .article .time {{ font-size: 0.8em; color: #7f8c8d; margin-top: 5px; }}
        .footer {{ text-align: center; margin-top: 30px; font-size: 0.8em; color: #7f8c8d; }}
        hr {{ margin: 20px 0; }}
    </style>
</head>
<body>
    <h1>📡 美伊谈判新闻实时监测</h1>
    <p>🕒 更新时间：{update_time} | 显示最近6小时内数据 | 每5分钟自动刷新</p>
    <h2>📰 新闻文章 ({article_count})</h2>
    {articles_html}
    <div class="footer">
        <hr>
        <p>数据来源：Dawn / ARY News | 自动抓取部署于 GitHub Actions | 淘汰超过6小时的内容</p>
    </div>
</body>
</html>"""
    
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
    
    # 巴基斯坦时区
    utc_now = datetime.now(timezone.utc)
    pkt_timezone = timezone(timedelta(hours=5))
    pkt_now = utc_now.astimezone(pkt_timezone)
    update_time = pkt_now.strftime("%Y-%m-%d %H:%M:%S PKT")
    
    html = html_template.format(
        update_time=update_time,
        article_count=len(articles),
        articles_html=articles_html
    )
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

# ==================== 主函数 ====================
def main():
    print(f"{datetime.now()} 开始抓取新闻首页...")
    all_items = load_items()
    existing_ids = {item["id"] for item in all_items}
    new_items = []
    
    for homepage in NEWS_URLS:
        print(f"\n处理首页: {homepage}")
        article_links = extract_article_links(homepage)
        print(f"  找到 {len(article_links)} 个链接")
        
        for idx, article_url in enumerate(article_links):
            print(f"  [{idx+1}/{len(article_links)}] 抓取: {article_url[:80]}...")
            article = fetch_article_detail(article_url)
            if article and article["id"] not in existing_ids:
                new_items.append(article)
                existing_ids.add(article["id"])
                print(f"    ✅ 新增: {article['title'][:50]}")
            else:
                print(f"    ⏭️ 已存在或失败")
            time.sleep(random.uniform(2, 4))  # 避免过快
    
    if new_items:
        all_items.extend(new_items)
        save_items(all_items)
        print(f"\n✅ 总计新增 {len(new_items)} 篇文章")
    else:
        print("\n📭 无新增文章")
    
    recent = filter_recent_items(all_items, hours=6)
    generate_html(recent)
    print(f"\n✅ 已生成 {HTML_FILE}，包含 {len(recent)} 条近期文章")
    print("完成")

if __name__ == "__main__":
    main()

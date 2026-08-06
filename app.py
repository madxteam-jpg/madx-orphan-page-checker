import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse
import xml.etree.ElementTree as ET
import pandas as pd
import re

def clean_url(url):
    """
    Strips www, trailing slashes, query strings, and fragments for accurate link matching.
    e.g., 'https://www.upfluence.com/platform/feature-find-influencers/' -> 'upfluence.com/platform/feature-find-influencers'
    """
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().replace('www.', '')
    path = parsed.path.rstrip('/')
    
    return f"{netloc}{path}"

def fetch_all_sitemap_urls(sitemap_url, max_urls=300, visited_sitemaps=None):
    """Recursively fetches URLs from sitemaps, including sitemap index files."""
    if visited_sitemaps is None:
        visited_sitemaps = set()
    
    if sitemap_url in visited_sitemaps or len(visited_sitemaps) > 10:
        return []
    
    visited_sitemaps.add(sitemap_url)
    found_urls = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SingleSetOrphanChecker/2.0"}

    try:
        resp = requests.get(sitemap_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            # Clean namespace tags from XML
            xml_content = re.sub(r'xmlns="[^"]+"', '', resp.text)
            root = ET.fromstring(xml_content)

            # Check if this is a sitemap index file containing child sitemaps
            sub_sitemaps = root.findall('.//sitemap/loc')
            if sub_sitemaps:
                for sub in sub_sitemaps:
                    if sub.text and len(found_urls) < max_urls:
                        found_urls.extend(fetch_all_sitemap_urls(sub.text.strip(), max_urls, visited_sitemaps))
            else:
                # Regular sitemap containing page URLs
                pages = root.findall('.//url/loc')
                for page in pages:
                    if page.text:
                        found_urls.append(page.text.strip())
                        if len(found_urls) >= max_urls:
                            break
    except Exception:
        pass

    return list(set(found_urls))

def run_orphan_check(target_urls, custom_sources=None, sitemap_url=None, max_scan=200):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SingleSetOrphanChecker/2.0"}
    
    # 1. Normalize target URLs mapping
    target_map = {clean_url(u): u.strip() for u in target_urls if u.strip()}
    target_cleans = set(target_map.keys())

    # 2. Gather source pages to crawl
    sources_to_crawl = set()
    
    if custom_sources:
        for src in custom_sources:
            if src.strip():
                sources_to_crawl.add(src.strip())
                
    if sitemap_url and sitemap_url.strip():
        discovered = fetch_all_sitemap_urls(sitemap_url.strip(), max_urls=max_scan)
        sources_to_crawl.update(discovered)
        
    # Fallback to domain root if no sources gathered
    if not sources_to_crawl and target_urls:
        first_parsed = urlparse(target_urls[0])
        sources_to_crawl.add(f"{first_parsed.scheme}://{first_parsed.netloc}")

    sources_list = list(sources_to_crawl)[:max_scan]
    inbound_db = {clean: [] for clean in target_cleans}

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_sources = len(sources_list)

    # 3. Crawl sources and search for links
    for idx, source_url in enumerate(sources_list):
        status_text.text(f"Scanning source ({idx + 1}/{total_sources}): {source_url}")
        progress_bar.progress((idx + 1) / total_sources)
        
        try:
            resp = requests.get(source_url, headers=headers, timeout=6)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                source_clean = clean_url(source_url)
                
                for a_tag in soup.find_all('a', href=True):
                    raw_href = a_tag['href']
                    full_href = urljoin(source_url, raw_href)
                    href_clean = clean_url(full_href)

                    # Match against target list (ignoring self-referencing links)
                    if href_clean in target_cleans and href_clean != source_clean:
                        parent_text = a_tag.parent.get_text(separator=' ', strip=True) if a_tag.parent else ""
                        snippet = parent_text[:160] + "..." if len(parent_text) > 160 else parent_text

                        inbound_db[href_clean].append({
                            "source_url": source_url,
                            "anchor_text": a_tag.get_text(strip=True) or "[Image / No Text]",
                            "raw_href": raw_href,
                            "context_snippet": snippet
                        })
        except Exception:
            continue

    status_text.empty()
    progress_bar.empty()

    # 4. Format Results
    results = []
    for clean_u, original_u in target_map.items():
        refs = inbound_db[clean_u]
        results.append({
            "target_url": original_u,
            "is_orphan": "Yes" if len(refs) == 0 else "No",
            "inbound_count": len(refs),
            "references": refs
        })

    return results, len(sources_list)

# --- Streamlit UI ---
st.set_page_config(page_title="Orphan Page Checker", layout="wide")

st.title("🔗 Batch Orphan Page Checker")

col_left, col_right = st.columns(2)

with col_left:
    user_targets = st.text_area(
        "1. Target URLs to Check (One per line):",
        height=180,
        placeholder="https://www.upfluence.com/platform/feature-find-influencers"
    )

with col_right:
    custom_sitemap = st.text_input(
        "2a. Sitemap URL (Optional - Auto discovers blog posts):",
        placeholder="https://www.upfluence.com/sitemap.xml"
    )
    user_sources = st.text_area(
        "2b. Specific Source URLs to Crawl (Optional - One per line):",
        height=100,
        placeholder="https://www.upfluence.com/influencer-marketing/how-to-find-the-perfect-influencers-for-your-niche"
    )

max_scan = st.slider("Max source pages to crawl:", min_value=10, max_value=500, value=150)

if st.button("Check Orphan Status", type="primary"):
    targets = [t.strip() for t in user_targets.splitlines() if t.strip()]
    sources = [s.strip() for s in user_sources.splitlines() if s.strip()]

    if not targets:
        st.warning("Please paste at least one target URL to check.")
    else:
        results, scanned_count = run_orphan_check(
            target_urls=targets,
            custom_sources=sources,
            sitemap_url=custom_sitemap,
            max_scan=max_scan
        )

        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        linked = len(results) - orphans

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Sources Crawled", scanned_count)
        m2.metric("Total Checked", len(results))
        m3.metric("Orphan Pages", orphans)
        m4.metric("Linked Pages", linked)

        st.markdown("---")

        df = pd.DataFrame([
            {
                "Target URL": r["target_url"],
                "Is Orphan?": r["is_orphan"],
                "Inbound Links Found": r["inbound_count"]
            }
            for r in results
        ])
        st.dataframe(df, use_container_width=True)

        st.markdown("### Found Link Evidence")
        for item in results:
            if item["is_orphan"] == "No":
                with st.expander(f"🟢 LINKED ({item['inbound_count']} references) — {item['target_url']}"):
                    for ref in item["references"]:
                        st.markdown(f"**Source Page:** [{ref['source_url']}]({ref['source_url']})")
                        st.markdown(f"- **Anchor Text:** `{ref['anchor_text']}`")
                        st.markdown(f"- **Raw Href:** `{ref['raw_href']}`")
                        st.markdown(f"- **Context:** *\"{ref['context_snippet']}\"*")
                        st.divider()
            else:
                with st.expander(f"🔴 ORPHAN — {item['target_url']}"):
                    st.warning("No inbound links pointing to this page were found across any scanned source pages.")

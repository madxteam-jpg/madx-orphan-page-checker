import streamlit as st
from curl_cffi import requests as c_requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import re

def clean_url(url):
    """Strips protocol, www, trailing slashes, query parameters, and fragments."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().replace('www.', '')
    path = parsed.path.rstrip('/')
    return f"{netloc}{path}"

def fetch_page_content(url):
    """Fetches HTML using Chrome TLS impersonation to bypass Cloudflare / bot blocks."""
    try:
        # Impersonate Chrome 120 browser fingerprint
        resp = c_requests.get(url, impersonate="chrome120", timeout=10, follow_redirects=True)
        return resp.status_code, resp.text
    except Exception as e:
        return None, str(e)

def fetch_sitemap_urls(sitemap_url, max_urls=300, visited=None):
    """Recursively parses sitemaps using browser impersonation."""
    if visited is None:
        visited = set()
    if sitemap_url in visited or len(visited) > 10:
        return []
    
    visited.add(sitemap_url)
    found_urls = []
    
    status, html_content = fetch_page_content(sitemap_url)
    if status == 200 and html_content:
        try:
            clean_xml = re.sub(r'xmlns="[^"]+"', '', html_content)
            root = ET.fromstring(clean_xml)

            sub_sitemaps = root.findall('.//sitemap/loc')
            if sub_sitemaps:
                for sub in sub_sitemaps:
                    if sub.text and len(found_urls) < max_urls:
                        found_urls.extend(fetch_sitemap_urls(sub.text.strip(), max_urls, visited))
            else:
                pages = root.findall('.//url/loc')
                for page in pages:
                    if page.text:
                        found_urls.append(page.text.strip())
                        if len(found_urls) >= max_urls:
                            break
        except Exception:
            pass
            
    return list(set(found_urls))

def check_orphans(target_urls, custom_sources=None, sitemap_url=None, max_scan=200):
    target_map = {clean_url(u): u.strip() for u in target_urls if u.strip()}
    target_cleans = set(target_map.keys())

    # Build source list
    sources_to_crawl = []
    if custom_sources:
        sources_to_crawl.extend([s.strip() for s in custom_sources if s.strip()])
        
    if sitemap_url and sitemap_url.strip():
        discovered = fetch_sitemap_urls(sitemap_url.strip(), max_urls=max_scan)
        sources_to_crawl.extend(discovered)

    # Deduplicate keeping order
    seen = set()
    unique_sources = [x for x in sources_to_crawl if not (x in seen or seen.add(x))][:max_scan]

    inbound_db = {clean: [] for clean in target_cleans}
    crawl_audit = []

    progress_bar = st.progress(0)
    status_text = st.empty()
    total = len(unique_sources)

    for idx, source_url in enumerate(unique_sources):
        status_text.text(f"Scanning ({idx + 1}/{total}): {source_url}")
        progress_bar.progress((idx + 1) / total)
        
        status_code, html = fetch_page_content(source_url)
        
        if status_code == 200 and html:
            soup = BeautifulSoup(html, 'html.parser')
            source_clean = clean_url(source_url)
            links_found = 0

            for a_tag in soup.find_all('a', href=True):
                raw_href = a_tag['href']
                full_href = urljoin(source_url, raw_href)
                href_clean = clean_url(full_href)

                if href_clean in target_cleans and href_clean != source_clean:
                    links_found += 1
                    parent_text = a_tag.parent.get_text(separator=' ', strip=True) if a_tag.parent else ""
                    snippet = parent_text[:160] + "..." if len(parent_text) > 160 else parent_text

                    inbound_db[href_clean].append({
                        "source_url": source_url,
                        "anchor_text": a_tag.get_text(strip=True) or "[Image / Empty Anchor]",
                        "raw_href": raw_href,
                        "context_snippet": snippet
                    })

            crawl_audit.append({"source_url": source_url, "status": "200 OK", "targets_linked": links_found})
        else:
            crawl_audit.append({"source_url": source_url, "status": f"Failed ({status_code})", "targets_linked": 0})

    status_text.empty()
    progress_bar.empty()

    results = []
    for clean_u, original_u in target_map.items():
        refs = inbound_db[clean_u]
        results.append({
            "target_url": original_u,
            "is_orphan": "Yes" if len(refs) == 0 else "No",
            "inbound_count": len(refs),
            "references": refs
        })

    return results, crawl_audit

# --- Streamlit UI ---
st.set_page_config(page_title="Orphan Checker", layout="wide")
st.title("🔗 Batch Orphan Page Checker (Cloudflare-Bypass Enabled)")

col1, col2 = st.columns(2)

with col1:
    user_targets = st.text_area(
        "1. Target URLs to Check:",
        height=180,
        value="https://www.upfluence.com/platform/feature-find-influencers"
    )

with col2:
    user_sources = st.text_area(
        "2. Source URLs to Crawl (Paste referring pages or blog posts here):",
        height=120,
        value="https://www.upfluence.com/influencer-marketing/how-to-find-the-perfect-influencers-for-your-niche"
    )
    custom_sitemap = st.text_input(
        "3. Optional Sitemap XML URL:",
        value="https://www.upfluence.com/sitemap.xml"
    )

max_scan = st.slider("Max source pages to scan:", 10, 500, 100)

if st.button("Run Check", type="primary"):
    targets = [t.strip() for t in user_targets.splitlines() if t.strip()]
    sources = [s.strip() for s in user_sources.splitlines() if s.strip()]

    if not targets:
        st.error("Please enter at least one target URL.")
    else:
        results, crawl_audit = check_orphans(
            target_urls=targets,
            custom_sources=sources,
            sitemap_url=custom_sitemap,
            max_scan=max_scan
        )

        # Overview Metrics
        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Targets", len(results))
        m2.metric("Orphan Pages", orphans)
        m3.metric("Linked Pages", len(results) - orphans)

        st.markdown("---")
        st.subheader("Results")

        df = pd.DataFrame([
            {"Target URL": r["target_url"], "Is Orphan?": r["is_orphan"], "Inbound Links Found": r["inbound_count"]}
            for r in results
        ])
        st.dataframe(df, use_container_width=True)

        st.markdown("### Evidence Breakdown")
        for item in results:
            if item["is_orphan"] == "No":
                with st.expander(f"🟢 LINKED ({item['inbound_count']} links) — {item['target_url']}"):
                    for ref in item["references"]:
                        st.markdown(f"**Source:** [{ref['source_url']}]({ref['source_url']})")
                        st.markdown(f"- **Anchor Text:** `{ref['anchor_text']}`")
                        st.markdown(f"- **Context:** *\"{ref['context_snippet']}\"*")
                        st.divider()
            else:
                with st.expander(f"🔴 ORPHAN — {item['target_url']}"):
                    st.warning("No incoming links detected on the scanned source pages.")

        st.markdown("---")
        with st.expander("🔍 Diagnostic Log (Verify Crawled Source Pages)"):
            st.dataframe(pd.DataFrame(crawl_audit), use_container_width=True)

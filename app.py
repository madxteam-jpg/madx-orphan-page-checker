import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import pandas as pd

def normalize_url(url):
    """Normalizes URLs to ensure accurate comparison across trailing slashes/protocols."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path.lower()}"

def get_sitemap_urls(domain_url):
    """Attempts to fetch all site URLs from standard sitemap locations."""
    sitemap_candidates = [
        f"{domain_url}/sitemap.xml",
        f"{domain_url}/sitemap_index.xml"
    ]
    discovered_urls = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SingleSetOrphanChecker/1.0"}

    for sitemap_url in sitemap_candidates:
        try:
            resp = requests.get(sitemap_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                # Parse XML for <loc> tags
                root = ET.fromstring(resp.content)
                for elem in root.iter():
                    if elem.tag.endswith('loc') and elem.text:
                        loc = elem.text.strip()
                        if not loc.endswith('.xml'):
                            discovered_urls.add(loc)
                if discovered_urls:
                    break
        except Exception:
            continue
            
    # Fallback to domain root if sitemap isn't found
    if not discovered_urls:
        discovered_urls.add(domain_url)
        
    return list(discovered_urls)

def check_orphans_against_site(target_urls, max_sources_to_scan=100):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SingleSetOrphanChecker/1.0"}
    
    # Map normalized target URLs
    target_map = {normalize_url(u): u for u in target_urls}
    target_norms = set(target_map.keys())

    # Extract primary domain from first target URL
    first_parsed = urlparse(list(target_map.keys())[0])
    domain_root = f"{first_parsed.scheme}://{first_parsed.netloc}"

    st.info(f"🔍 Automatically discovering site source pages for `{domain_root}`...")
    source_urls = get_sitemap_urls(domain_root)[:max_sources_to_scan]
    
    inbound_db = {norm_url: [] for norm_url in target_norms}
    crawled_sources = 0

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_sources = len(source_urls)

    for idx, source_url in enumerate(source_urls):
        status_text.text(f"Scanning source page ({idx + 1}/{total_sources}): {source_url}")
        progress_bar.progress((idx + 1) / total_sources)
        
        try:
            resp = requests.get(source_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                crawled_sources += 1
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                for a_tag in soup.find_all('a', href=True):
                    raw_href = a_tag['href']
                    full_href = urljoin(source_url, raw_href)
                    target_norm = normalize_url(full_href)

                    # Check if this source page links to one of our target URLs
                    if target_norm in target_norms:
                        # Ensure it's not a self-link
                        if normalize_url(source_url) != target_norm:
                            parent_text = a_tag.parent.get_text(separator=' ', strip=True) if a_tag.parent else ""
                            snippet = parent_text[:160] + "..." if len(parent_text) > 160 else parent_text

                            inbound_db[target_norm].append({
                                "source_url": source_url,
                                "anchor_text": a_tag.get_text(strip=True) or "[No Anchor Text]",
                                "raw_href": raw_href,
                                "context_snippet": snippet
                            })
        except Exception:
            continue

    status_text.empty()
    progress_bar.empty()

    # Compile final results
    results = []
    for norm_url, raw_url in target_map.items():
        references = inbound_db[norm_url]
        is_orphan = len(references) == 0
        
        results.append({
            "target_url": raw_url,
            "is_orphan": "Yes" if is_orphan else "No",
            "inbound_count": len(references),
            "references": references
        })

    return results, crawled_sources

# --- Streamlit UI ---
st.set_page_config(page_title="Accurate Orphan Page Checker", layout="wide")

st.title("🔗 Accurate Orphan Page Checker")
st.markdown("Paste your list of target URLs below. The app automatically fetches your site's pages to scan for incoming links pointing to them.")

user_input = st.text_area(
    "Target URLs to Check (One per line):",
    height=200,
    placeholder="https://example.com/blog/target-page-1\nhttps://example.com/target-page-2"
)

max_scan = st.number_input("Max source pages to scan from sitemap:", min_value=10, max_value=500, value=100)

if st.button("Check Orphan Status", type="primary"):
    target_urls = [line.strip() for line in user_input.splitlines() if line.strip()]
    
    if not target_urls:
        st.warning("Please paste at least one target URL.")
    else:
        results, scanned_count = check_orphans_against_site(target_urls, max_scan)
        
        # Metrics
        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        linked = len(results) - orphans

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Scanned Site Pages", scanned_count)
        col2.metric("Total Checked", len(results))
        col3.metric("Orphan Pages", orphans, delta_color="inverse")
        col4.metric("Linked Pages", linked)

        st.markdown("---")
        
        # Summary Table
        df = pd.DataFrame([
            {
                "Target URL": r["target_url"],
                "Is Orphan?": r["is_orphan"],
                "Inbound Links Found": r["inbound_count"]
            }
            for r in results
        ])
        
        st.dataframe(df, use_container_width=True)

        st.markdown("---")
        st.subheader("Inbound Link Evidence")

        # Breakdown per target URL
        for item in results:
            if item["is_orphan"] == "No":
                badge = f"🟢 LINKED ({item['inbound_count']} links found)"
                with st.expander(f"{badge} — {item['target_url']}"):
                    for ref in item["references"]:
                        st.markdown(f"**Source Page:** [{ref['source_url']}]({ref['source_url']})")
                        st.markdown(f"- **Anchor Text:** `{ref['anchor_text']}`")
                        st.markdown(f"- **Context Snippet:** *\"{ref['context_snippet']}\"*")
                        st.divider()
            else:
                with st.expander(f"🔴 ORPHAN — {item['target_url']}"):
                    st.warning("No incoming links pointing to this page were found across any scanned source pages.")

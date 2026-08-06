import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import pandas as pd

def normalize_url(url):
    """Normalizes URLs for reliable matching across relative/absolute variants."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    parsed = urlparse(url)
    # Strip trailing slash and lowercase netloc/path for matching
    path = parsed.path.rstrip('/')
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path.lower()}"

def crawl_and_check_orphans(url_list):
    """
    Crawls every URL in the list, maps all links found between them,
    and returns orphan status with context snippets.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SingleSetOrphanChecker/1.0"
    }
    
    # Map normalized URLs back to their original user input representation
    url_map = {normalize_url(u): u for u in url_list}
    normalized_targets = set(url_map.keys())
    
    # Store incoming links: { target_norm_url: [ {source_url, anchor_text, raw_href, snippet}, ... ] }
    inbound_db = {norm_url: [] for norm_url in normalized_targets}
    crawled_status = {}

    progress_bar = st.progress(0)
    status_text = st.empty()
    total_urls = len(url_list)

    for idx, (norm_url, raw_url) in enumerate(url_map.items()):
        status_text.text(f"Crawling ({idx + 1}/{total_urls}): {raw_url}")
        progress_bar.progress((idx + 1) / total_urls)
        
        try:
            response = requests.get(raw_url, headers=headers, timeout=8)
            crawled_status[raw_url] = f"HTTP {response.status_code}"
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                for a_tag in soup.find_all('a', href=True):
                    raw_href = a_tag['href']
                    full_href = urljoin(raw_url, raw_href)
                    target_norm = normalize_url(full_href)

                    # Only record links pointing to another URL in the input list (excluding self-links)
                    if target_norm in normalized_targets and target_norm != norm_url:
                        parent_text = a_tag.parent.get_text(separator=' ', strip=True) if a_tag.parent else ""
                        snippet = parent_text[:160] + "..." if len(parent_text) > 160 else parent_text

                        inbound_db[target_norm].append({
                            "source_url": raw_url,
                            "anchor_text": a_tag.get_text(strip=True) or "[No Anchor Text]",
                            "raw_href": raw_href,
                            "context_snippet": snippet
                        })
        except Exception as e:
            crawled_status[raw_url] = f"Error: {type(e).__name__}"

    status_text.empty()
    progress_bar.empty()

    # Build results dataset
    results = []
    for norm_url, raw_url in url_map.items():
        found_links = inbound_db[norm_url]
        is_orphan = len(found_links) == 0
        
        results.append({
            "raw_url": raw_url,
            "is_orphan": "Yes" if is_orphan else "No",
            "inbound_count": len(found_links),
            "crawl_status": crawled_status.get(raw_url, "Unknown"),
            "references": found_links
        })

    return results

# --- Streamlit UI ---
st.set_page_config(page_title="Orphan Page Checker", layout="wide")

st.title("🔗 Batch Orphan Page Checker")
st.markdown("Paste a list of URLs below (one per line). The app will crawl them and check if each page receives inbound links from any **other** page in the list.")

user_input = st.text_area(
    "Paste URLs (One per line):",
    height=200,
    placeholder="https://example.com/page-1\nhttps://example.com/page-2\nhttps://example.com/page-3"
)

if st.button("Check Orphan Status", type="primary"):
    urls = [line.strip() for line in user_input.splitlines() if line.strip()]
    
    if not urls:
        st.warning("Please paste at least one valid URL to analyze.")
    elif len(urls) == 1:
        st.info("You provided only 1 URL. Without other source URLs to scan, it will be marked as an orphan by default.")
    
    if urls:
        results = crawl_and_check_orphans(urls)
        
        # Summary KPI Row
        total = len(results)
        orphans = sum(1 for r in results if r["is_orphan"] == "Yes")
        linked = total - orphans

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Checked", total)
        col2.metric("Orphan Pages", orphans, delta_color="inverse")
        col3.metric("Linked Pages", linked)

        st.markdown("---")
        st.subheader("Results Overview")

        # Create Summary DataFrame
        summary_data = [
            {
                "URL": r["raw_url"],
                "Is Orphan?": r["is_orphan"],
                "Inbound Links Found": r["inbound_count"],
                "Crawl Status": r["crawl_status"]
            }
            for r in results
        ]
        df = pd.DataFrame(summary_data)
        
        # Display Data Table with Download Option
        st.dataframe(df, use_container_width=True)
        st.download_button(
            "Download CSV Summary",
            data=df.to_csv(index=False),
            file_name="orphan_check_results.csv",
            mime="text/csv"
        )

        st.markdown("---")
        st.subheader("Detailed Link Context")

        # Detailed Accordion Breakdown
        for item in results:
            badge = "🔴 ORPHAN" if item["is_orphan"] == "Yes" else f"🟢 LINKED ({item['inbound_count']} incoming)"
            
            with st.expander(f"{badge} — {item['raw_url']}"):
                if item["references"]:
                    st.markdown("**Incoming Link References Found:**")
                    for idx, ref in enumerate(item["references"], 1):
                        st.markdown(f"**{idx}. Source Page:** [{ref['source_url']}]({ref['source_url']})")
                        st.markdown(f"- **Anchor Text:** `{ref['anchor_text']}`")
                        st.markdown(f"- **Href:** `{ref['raw_href']}`")
                        st.markdown(f"- **Surrounding Context:** *\"{ref['context_snippet']}\"*")
                        st.divider()
                else:
                    st.warning("No incoming links pointing to this page were found on any of the other scanned URLs.")

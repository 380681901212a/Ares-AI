import re
import time
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from playwright.sync_api import sync_playwright

from llm_config import get_text_llm
from langchain_core.prompts import ChatPromptTemplate

class OfferSchema(BaseModel):
    product: str = Field(description="Exact product name and model")
    price: str = Field(description="Price with currency (e.g. '19999 грн')")
    url: str = Field(description="Direct URL to the product. If relative, prepend the domain.")

class OfferExtractionResult(BaseModel):
    offers: list[OfferSchema] = Field(description="List of extracted offers, up to 5.")

def fetch_page_content_playwright(url: str) -> str:
    print(f"  [OfferExtractor] Launching Playwright to fetch {url}...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            # Block heavy resources to speed up and avoid bot detection loops
            def route_interceptor(route):
                if route.request.resource_type in ["image", "media", "font"]:
                    route.abort()
                else:
                    route.continue_()
            
            page.route("**/*", route_interceptor)
            
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            # Wait a short moment for JS-rendered lists to populate (e.g. Rozetka products)
            page.wait_for_timeout(3000)
            
            html = page.content()
            browser.close()
            return html
    except Exception as e:
        print(f"  [OfferExtractor] Playwright failed: {e}")
        return ""

def extract_offers_from_search_results(search_results: str, budget_max: int | None = None) -> str:
    """Extracts links from search results, fetches their content, and uses LLM to parse real product offers."""
    urls = re.findall(r'Source:\s*(https?://[^\s]+)', search_results)
    
    unique_urls = []
    for u in urls:
        if u not in unique_urls:
            unique_urls.append(u)
            
    if not unique_urls:
        return ""
    
    extracted_data = []
    llm = get_text_llm()
    structured_llm = llm.with_structured_output(OfferExtractionResult)
    
    budget_note = f"The user has a maximum budget of {budget_max}. Try to find offers near or below this budget." if budget_max else "Find the best actual offers."
    
    system_prompt = (
        "You are an Offer Extraction tool. Analyze the webpage text and extract up to 5 real product offers.\n"
        "You must return ONLY actual products with prices and exact URLs.\n"
        f"{budget_note}\n"
        "Ignore accessories or unrelated items if the user asked for a main device (e.g., if they want a phone, ignore phone cases)."
    )
    
    # Process only top 3 URLs to save execution time
    for url in unique_urls[:3]:
        html = fetch_page_content_playwright(url)
        if not html:
            continue
            
        soup = BeautifulSoup(html, "html.parser")
        for script in soup(["script", "style", "noscript", "svg"]):
            script.extract()
            
        # Extract text, compress whitespace
        text = soup.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text)
        
        # Limit to ~25000 chars to avoid massive context
        text = text[:25000]
        
        try:
            prompt = ChatPromptTemplate.from_messages([
                ("system", system_prompt),
                ("user", f"Source URL: {url}\n\nWebpage text:\n{text}")
            ])
            chain = prompt | structured_llm
            result = chain.invoke({})
            
            if result.offers:
                for off in result.offers:
                    extracted_data.append(f"Product: {off.product}\nPrice: {off.price}\nURL: {off.url}")
        except Exception as e:
            print(f"  [OfferExtractor] LLM extraction error for {url}: {e}")
            
    if extracted_data:
        return "\n\n".join(extracted_data)
        
    return ""

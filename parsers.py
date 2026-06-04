import os
import zipfile
import xml.etree.ElementTree as ET
import posixpath
from html.parser import HTMLParser
from pypdf import PdfReader

class EpubTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.ignore_tags = {'style', 'script', 'head', 'noscript'}
        self.ignore_depth = 0
        self.block_tags = {
            'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
            'li', 'tr', 'blockquote', 'section', 'article', 
            'aside', 'header', 'footer', 'br', 'pre'
        }
        
    def handle_starttag(self, tag, attrs):
        if tag in self.ignore_tags:
            self.ignore_depth += 1
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
    def handle_endtag(self, tag):
        if tag in self.ignore_tags:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
            
        if self.ignore_depth > 0:
            return
            
        if tag in self.block_tags:
            self.text_parts.append('\n')
            
    def handle_data(self, data):
        if self.ignore_depth > 0:
            return
        self.text_parts.append(data)
        
    def get_text(self):
        raw_text = "".join(self.text_parts)
        lines = []
        for line in raw_text.split('\n'):
            stripped = line.strip()
            lines.append(stripped)
            
        cleaned_lines = []
        for line in lines:
            if line:
                cleaned_lines.append(line)
            else:
                if cleaned_lines and cleaned_lines[-1] != "":
                    cleaned_lines.append("")
                    
        return "\n".join(cleaned_lines).strip()

def extract_epub_text(file_path: str) -> str:
    """Extracts plain text from an EPUB ZIP archive following spine manifest order."""
    full_text = []
    try:
        with zipfile.ZipFile(file_path, 'r') as epub:
            # 1. Locate the OPF root file using container.xml
            opf_path = None
            try:
                if "META-INF/container.xml" in epub.namelist():
                    container_data = epub.read("META-INF/container.xml")
                    root = ET.fromstring(container_data)
                    
                    namespaces = {'ns': 'urn:oasis:names:tc:opendocument:xmlns:container'}
                    rootfile_el = root.find('.//ns:rootfile', namespaces)
                    if rootfile_el is not None:
                        opf_path = rootfile_el.attrib.get('full-path')
                    else:
                        for elem in root.iter():
                            if elem.tag.split('}')[-1] == 'rootfile':
                                opf_path = elem.attrib.get('full-path')
                                break
            except Exception:
                pass
            
            # Fallback to searching zip file list if not resolved
            if not opf_path:
                opf_path = next((f for f in epub.namelist() if f.endswith('.opf')), None)
            
            html_files_in_order = []
            
            # 2. Parse OPF manifest and spine
            if opf_path and opf_path in epub.namelist():
                try:
                    opf_dir = posixpath.dirname(opf_path)
                    opf_data = epub.read(opf_path)
                    opf_root = ET.fromstring(opf_data)
                    
                    # Manifest mapping: id -> (href, media-type)
                    manifest_items = {}
                    for elem in opf_root.iter():
                        tag_name = elem.tag.split('}')[-1]
                        if tag_name == 'item':
                            item_id = elem.attrib.get('id')
                            item_href = elem.attrib.get('href')
                            item_media_type = elem.attrib.get('media-type')
                            if item_id and item_href:
                                manifest_items[item_id] = (item_href, item_media_type)
                                
                    # Spine ordering
                    spine_ids = []
                    for elem in opf_root.iter():
                        tag_name = elem.tag.split('}')[-1]
                        if tag_name == 'itemref':
                            idref = elem.attrib.get('idref')
                            if idref:
                                spine_ids.append(idref)
                                
                    # Map spine IDs to relative paths in the zip
                    for idref in spine_ids:
                        if idref in manifest_items:
                            href, media_type = manifest_items[idref]
                            if href:
                                href_clean = href.split('#')[0].split('?')[0]
                                if opf_dir:
                                    full_path = posixpath.normpath(posixpath.join(opf_dir, href_clean))
                                else:
                                    full_path = posixpath.normpath(href_clean)
                                
                                is_html = (
                                    (media_type and ('html' in media_type or 'xhtml' in media_type)) or
                                    full_path.endswith(('.html', '.xhtml', '.htm'))
                                )
                                if is_html and full_path in epub.namelist():
                                    if full_path not in html_files_in_order:
                                        html_files_in_order.append(full_path)
                except Exception:
                    pass
            
            # Fallback to alphabetical sorting of HTML files
            if not html_files_in_order:
                html_files_in_order = [f for f in epub.namelist() if f.endswith(('.html', '.xhtml', '.htm'))]
                html_files_in_order.sort()
            
            # 3. Extract text from HTML files in order
            for f in html_files_in_order:
                try:
                    content = epub.read(f).decode('utf-8', errors='ignore')
                    parser = EpubTextParser()
                    parser.feed(content)
                    text = parser.get_text()
                    if text:
                        full_text.append(text)
                except Exception:
                    pass
                    
    except Exception as zip_err:
        raise ValueError(f"Failed to read EPUB file: {zip_err}")
        
    return "\n\n".join(full_text)

def extract_pdf_text(file_path: str) -> str:
    """Extracts plain text from a PDF file using pypdf."""
    text_content = []
    try:
        reader = PdfReader(file_path)
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_content.append(text)
    except Exception as e:
        raise ValueError(f"Failed to read PDF file: {e}")
        
    return "\n\n".join(text_content)

def extract_txt_text(file_path: str) -> str:
    """Extracts plain text from a text or markdown file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception as e:
        raise ValueError(f"Failed to read text file: {e}")

def extract_text(file_path: str) -> str:
    """Extracts text from a file based on its extension."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
        
    ext = os.path.splitext(file_path)[1].lower()
    
    if ext == ".pdf":
        return extract_pdf_text(file_path)
    elif ext == ".epub":
        return extract_epub_text(file_path)
    elif ext in [".txt", ".md", ".markdown"]:
        return extract_txt_text(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

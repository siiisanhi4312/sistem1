"""
BFAR Shellfish Advisory PDF Parser
Extracts and parses bulletin data from BFAR Shellfish Advisory PDFs.
"""

import logging
import re
import sys
from datetime import datetime
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)

# Import fitz and provide helpful error message if not available
try:
    import fitz  # PyMuPDF
except ImportError as e:
    logger.warning(f"PyMuPDF import warning: {e}")
    fitz = None


class BfarPdfParser:
    """
    Robust parser for BFAR Shellfish Bulletins that dynamically identifies:
    - Bulletin number (e.g., SB 19, SB_19)
    - Advisory date
    - Affected shellfish areas
    - Shellfish status (PSP/red tide)
    - Important information/advisory statement
    """

    def __init__(self, pdf_filename: str = ""):
        self.pdf_filename = pdf_filename
        self.pdf_text = ""
        self.bulletin_data = {
            'bulletin_number': None,
            'date': None,
            'affected_areas': [],
            'status': "",
            'important_information': "",
        }

    def extract_pdf_text(self, pdf_content: bytes) -> str:
        """
        Extract text from PDF content using PyMuPDF.
        
        Args:
            pdf_content: Raw PDF bytes
            
        Returns:
            Extracted text string
            
        Raises:
            ValueError: If PyMuPDF is not available or PDF is invalid
        """
        if fitz is None:
            logger.error(f"PyMuPDF not available. Python: {sys.executable}")
            raise ValueError(
                "PyMuPDF (fitz) is not installed. Install it with: "
                "pip install PyMuPDF==1.24.8 or "
                "activate the virtual environment and install there."
            )

        try:
            pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")
            text_parts = []
            
            for page_num in range(len(pdf_doc)):
                page = pdf_doc[page_num]
                text_parts.append(page.get_text("text"))
            
            pdf_doc.close()
            self.pdf_text = "\n".join(text_parts)
            logger.debug(f"Successfully extracted {len(self.pdf_text)} characters from PDF")
            return self.pdf_text
            
        except Exception as exc:
            logger.error(f"Failed to extract PDF text: {exc}")
            raise ValueError(f"Failed to extract PDF text: {exc}") from exc

    def _extract_bulletin_number(self) -> Optional[str]:
        """
        Extract bulletin number from PDF text or filename.
        Examples: SB 19, SB_19, SB-19, Bulletin 19, Advisory 19, etc.
        """
        # Try extracting from PDF text first
        patterns = [
            r'(?:SB|SHELLFISH\s+BULLETIN|BULLETIN)\s*[:\-_]?\s*(\d+)',
            r'(?:advisory|circular)\s*(?:no\.?|number|#)?\s*[:\-_]?\s*(\d+)',
            r'bulletin\s*(?:no\.?|number|#)?\s*[:\-_]?\s*(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, self.pdf_text, re.IGNORECASE)
            if match:
                bulletin_num = match.group(1)
                logger.debug(f"Extracted bulletin number from PDF text: SB {bulletin_num}")
                return f"SB {bulletin_num}"
        
        # Fallback: try to extract from filename
        if self.pdf_filename:
            # Try patterns like SB_19, SB 19, SB-19, Bulletin 19, etc.
            match = re.search(
                r'(?:SB|SHELLFISH\s*[-_ ]?BULLETIN|BULLETIN)'
                r'\s*[-_:. ]*?(?:NO\.?\s*)?[-_:. ]*?(\d+)',
                self.pdf_filename,
                re.IGNORECASE,
            )
            if match:
                bulletin_num = match.group(1)
                logger.debug(f"Extracted bulletin number from filename: SB {bulletin_num}")
                return f"SB {bulletin_num}"
        
        logger.warning("Could not extract bulletin number from PDF or filename")
        return None

    def _extract_advisory_date(self) -> Optional[datetime]:
        """
        Extract advisory date from PDF text or filename.
        Handles various date formats: YYYY-MM-DD, MM/DD/YYYY, August 5, 2026, etc.
        """
        # Try common date patterns
        date_patterns = [
            # Day-first format: "01 June2026" or "05 August 2026"
            r'(?P<day>\d{1,2})\s*(?P<month>January|February|March|April|May|June|July|August|September|Sept|Sep|October|November|December)\s*(?P<year>20\d{2})',
            # Full date format: "August 5, 2026" or "August 5 2026"
            r'(?P<month>January|February|March|April|May|June|July|August|September|Sept|Sep|October|November|December)\s*(?P<day>\d{1,2}),?\s*(?P<year>20\d{2})',
            # ISO format: YYYY-MM-DD
            r'(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})',
            # US format: MM/DD/YYYY or MM-DD-YYYY
            r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})',
            # From filename: dated-August-5-2026
            r'dated[-\s]+(?P<month>January|February|March|April|May|June|July|August|September|Sept|Sep|October|November|December)[-\s]+(?P<day>\d{1,2})[-\s]+(?P<year>20\d{2})',
        ]
        
        month_map = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'sept': 9, 'sep': 9,
            'october': 10, 'november': 11, 'december': 12,
        }
        
        # Try text first
        for pattern in date_patterns:
            match = re.search(pattern, self.pdf_text, re.IGNORECASE)
            if match:
                try:
                    if match.groupdict().get('month'):
                        month_name = match.group('month').lower().rstrip('.')
                        month = month_map[month_name]
                        day = int(match.group('day'))
                        year = int(match.group('year'))
                        return datetime(year, month, day).date()
                    if len(match.groups()) == 2:
                        # Month name + year pattern
                        month_name = match.group(1).lower()
                        day = int(match.group(2))
                        year = int(match.group(3)) if len(match.groups()) == 3 else int(match.group(2))
                        
                        if month_name in month_map and year >= 2000:
                            month = month_map[month_name]
                            date_obj = datetime(year, month, day).date()
                            logger.debug(f"Extracted date from PDF text: {date_obj}")
                            return date_obj
                    elif len(match.groups()) == 3:
                        if int(match.group(1)) > 31:  # Year first format
                            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        else:  # Month/day first format
                            month, day, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                        
                        if 1 <= month <= 12 and 1 <= day <= 31 and 2000 <= year <= 2100:
                            date_obj = datetime(year, month, day).date()
                            logger.debug(f"Extracted date from PDF text: {date_obj}")
                            return date_obj
                except (ValueError, IndexError):
                    continue
        
        # Fallback: try filename
        if self.pdf_filename:
            for pattern in date_patterns:
                match = re.search(pattern, self.pdf_filename, re.IGNORECASE)
                if match:
                    try:
                        if match.groupdict().get('month'):
                            month = month_map[match.group('month').lower()]
                            day = int(match.group('day'))
                            year = int(match.group('year'))
                            return datetime(year, month, day).date()
                        if len(match.groups()) >= 2:
                            if 'dated' in self.pdf_filename.lower():
                                month_name = match.group(1).lower() if match.group(1) else None
                                day = int(match.group(2)) if match.group(2) else None
                                year = int(match.group(3)) if len(match.groups()) >= 3 else None
                                
                                if month_name and month_name in month_map and day and year:
                                    month = month_map[month_name]
                                    if 1 <= day <= 31 and 2000 <= year <= 2100:
                                        date_obj = datetime(year, month, day).date()
                                        logger.debug(f"Extracted date from filename: {date_obj}")
                                        return date_obj
                    except (ValueError, IndexError, TypeError):
                        continue
        
        logger.warning("Could not extract advisory date from PDF or filename")
        return None

    def _extract_affected_areas(self) -> List[str]:
        """
        Dynamically extract affected shellfish areas from PDF text.
        Looks for sections that list municipalities, provinces, or bay names.
        """
        areas = []
        
        # Common area identifiers in BFAR bulletins
        area_patterns = [
            # Look for "AFFECTED AREAS:" or "CLOSED AREAS:" sections
            r'(?:affected|closed|quarantine)\s+(?:area|province|municipality)s?:?\s*\n((?:[^\n]*\n){1,10}?)(?:\n\n|\Z)',
            # Look for lines with bay/province names
            r'(?:bay of|gulf of|strait of|province of)\s+([A-Za-z\s,]+?)(?:\n|$)',
        ]
        
        for pattern in area_patterns:
            matches = re.finditer(pattern, self.pdf_text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                text = match.group(1) if match.lastindex else match.group(0)
                # Split by common delimiters and clean up
                area_items = re.split(r'[,\n;]', text)
                for item in area_items:
                    item = item.strip()
                    # Filter out short items and common words
                    if item and len(item) > 3 and item.lower() not in ('and', 'or', 'the', 'including', 'from', 'to'):
                        areas.append(item)
        
        # If no structured areas found, look for specific province/municipality keywords
        if not areas:
            keywords = [
                'bohol', 'cebu', 'palawan', 'mindanao', 'luzon', 'visayas',
                'panay', 'negros', 'samar', 'leyte', 'bicol', 'calabarzon',
                'ilagan', 'zamboanga', 'lanao', 'cotabato', 'agusan', 'surigao',
                'davao', 'misamis', 'camiguin', 'dinagat',
                'manila bay', 'sulu sea', 'tawi-tawi', 'basilan', 'sulu',
                'quezon bay', 'tayabas bay', 'ragay gulf',
            ]
            for keyword in keywords:
                if re.search(r'\b' + keyword + r'\b', self.pdf_text, re.IGNORECASE):
                    areas.append(keyword.title())
        
        # Remove duplicates while preserving order
        unique_areas = []
        seen = set()
        for area in areas:
            area_lower = area.lower()
            if area_lower not in seen:
                unique_areas.append(area)
                seen.add(area_lower)
        
        logger.debug(f"Extracted {len(unique_areas)} affected areas: {unique_areas}")
        return unique_areas[:20]  # Limit to 20 areas

    def _extract_status(self) -> str:
        """
        Extract shellfish status information (PSP, red tide, etc.).
        Looks for status indicators and affected shellfish types.
        """
        status_parts = []
        
        # Look for PSP (Paralytic Shellfish Poisoning) status
        if re.search(r'PSP|paralytic shellfish poisoning', self.pdf_text, re.IGNORECASE):
            # Check for positive or negative
            context_pattern = r'(positive|negative|detected|not detected).*?PSP|PSP.*?(positive|negative|detected|not detected)'
            match = re.search(context_pattern, self.pdf_text, re.IGNORECASE)
            if match:
                status = match.group(1).lower()
                status_parts.append(f"PSP: {status.capitalize()}")
            else:
                status_parts.append("PSP Monitoring")
        
        # Look for red tide/HAB (Harmful Algal Bloom) status
        if re.search(r'red tide|harmful algal bloom|HAB', self.pdf_text, re.IGNORECASE):
            context_pattern = r'(positive|negative|detected|not detected|present|absent).*?(?:red tide|HAB)|(?:red tide|HAB).*?(positive|negative|detected|not detected|present|absent)'
            match = re.search(context_pattern, self.pdf_text, re.IGNORECASE)
            if match:
                status = match.group(1).lower()
                status_parts.append(f"Red Tide: {status.capitalize()}")
            else:
                status_parts.append("Red Tide Monitoring")
        
        # Look for fish kill events
        if re.search(r'fish kill|mortality|mass death', self.pdf_text, re.IGNORECASE):
            status_parts.append("Fish Kill Reported")
        
        # Look for shellfish types mentioned
        shellfish_types = ['mussel', 'oyster', 'clam', 'scallop', 'cockle', 'venus clam']
        affected_shellfish = [st for st in shellfish_types if re.search(r'\b' + st + r'\b', self.pdf_text, re.IGNORECASE)]
        if affected_shellfish:
            status_parts.append(f"Affected: {', '.join([s.capitalize() for s in affected_shellfish])}")
        
        status = "; ".join(status_parts) if status_parts else "No specific status found"
        logger.debug(f"Extracted status: {status}")
        return status

    def _extract_important_information(self) -> str:
        """
        Extract important advisory statements and recommendations.
        Looks for advisory statements, closures, and recommendations.
        """
        info_parts = []
        
        # Look for closure notices
        closure_pattern = r'(?:closure|closed|quarantine|suspended|prohibited).*?(?:until|for|period).*?\n'
        closure_match = re.search(closure_pattern, self.pdf_text, re.IGNORECASE)
        if closure_match:
            info_parts.append(closure_match.group(0).strip())
        
        # Look for advisory statements
        advisory_pattern = r'(?:advisory|recommend|advise).*?:?\s*([^\n]*(?:\n[^\n]*){0,3})'
        advisory_matches = re.finditer(advisory_pattern, self.pdf_text, re.IGNORECASE)
        for match in advisory_matches:
            statement = match.group(1).strip()
            if len(statement) > 20:  # Only include substantial statements
                info_parts.append(statement)
                if len(info_parts) >= 3:  # Limit to 3 major statements
                    break
        
        # Look for recommendation on not consuming shellfish
        if re.search(r'(?:do not|avoid|refrain from).*?(?:consume|eat|harvest)', self.pdf_text, re.IGNORECASE):
            info_parts.append("DO NOT CONSUME shellfish from affected areas")
        
        # Look for harvesting restrictions
        if re.search(r'(?:harvesting|gathering|collection).*?(?:prohibited|suspended|stopped)', self.pdf_text, re.IGNORECASE):
            info_parts.append("Harvesting is prohibited from affected areas")
        
        # Get first substantial paragraph as fallback
        if not info_parts:
            paragraphs = re.split(r'\n\n+', self.pdf_text)
            for para in paragraphs[1:6]:  # Skip first paragraph (usually header)
                para = para.strip()
                if 80 < len(para) < 500:  # Reasonable paragraph length
                    info_parts.append(para)
                    break
        
        important_info = "\n\n".join(info_parts[:2])  # Limit to 2 sections
        logger.debug(f"Extracted important information ({len(important_info)} chars)")
        return important_info

    def parse(self, pdf_content: bytes) -> Dict[str, any]:
        """
        Parse BFAR Shellfish Bulletin PDF.
        
        Args:
            pdf_content: Raw PDF bytes
            
        Returns:
            Dictionary with extracted bulletin data:
            {
                'bulletin_number': str or None,
                'date': datetime.date or None,
                'affected_areas': list of str,
                'status': str,
                'important_information': str,
            }
            
        Raises:
            ValueError: If PDF text extraction fails
        """
        try:
            # Extract text from PDF
            self.extract_pdf_text(pdf_content)
            
            # Parse all fields
            self.bulletin_data = {
                'bulletin_number': self._extract_bulletin_number(),
                'date': self._extract_advisory_date(),
                'affected_areas': self._extract_affected_areas(),
                'status': self._extract_status(),
                'important_information': self._extract_important_information(),
            }
            
            logger.info(f"Successfully parsed bulletin: {self.bulletin_data['bulletin_number']} "
                       f"dated {self.bulletin_data['date']}")
            
            return self.bulletin_data
            
        except Exception as exc:
            logger.error(f"PDF parsing failed: {exc}")
            raise

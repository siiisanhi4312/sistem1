import hashlib
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.utils import timezone

from prediction.models import ShellfishAdvisory
from prediction.services.bfar_pdf_parser import BfarPdfParser
from prediction.services.firebase_bulletins import firebase_bulletins

logger = logging.getLogger(__name__)
_BFAR_SYNC_LOCK = threading.Lock()

DEFAULT_BFAR_URL = 'https://www.bfar.da.gov.ph/'
REQUEST_TIMEOUT = 30
SYNC_INTERVAL = timedelta(hours=6)
WP_SEARCH_URL = 'https://www.bfar.da.gov.ph/wp-json/wp/v2/search'
WP_MEDIA_URL = 'https://www.bfar.da.gov.ph/wp-json/wp/v2/media'


class _BfarLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            self._href = dict(attrs).get('href')
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self._href:
            self.links.append((' '.join(self._text).strip(), self._href))
            self._href = None


class _BfarTableParser(HTMLParser):
    """Read bulletin archive rows, including dates in sibling table cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None
        self._link = None
        self._link_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'tr':
            self._row = {'cells': [], 'links': []}
        elif self._row is not None and tag in ('td', 'th'):
            self._cell = {'text': [], 'links': []}
        elif self._cell is not None and tag == 'a':
            self._link = dict(attrs).get('href')
            self._link_text = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell['text'].append(data)
        if self._link is not None:
            self._link_text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'a' and self._link is not None:
            self._cell['links'].append((' '.join(self._link_text).strip(), self._link))
            self._link = None
            self._link_text = []
        elif tag in ('td', 'th') and self._cell is not None:
            self._cell['value'] = re.sub(r'\s+', ' ', ' '.join(self._cell['text'])).strip()
            self._row['cells'].append(self._cell)
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row['cells']:
                self.rows.append(self._row)
            self._row = None


def _parse_date(value):
    value = re.sub(r'\s+', ' ', value or '').strip()
    named = re.search(
            r'(?<!\d)(\d{1,2})[\s_-]+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s_-]+(20\d{2})(?!\d)',
        value,
        re.IGNORECASE,
    )
    if not named:
        named = re.search(
            r'(?<![A-Za-z])(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t|tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[\s_-]+(\d{1,2}),?[\s_-]+(20\d{2})(?!\d)',
            value,
            re.IGNORECASE,
        )
        if named:
            month, day, year = named.group(1), named.group(2), named.group(3)
        else:
            month = day = year = None
    else:
        day, month, year = named.group(1), named.group(2), named.group(3)

    if month:
        months = {name.lower(): index for index, name in enumerate(
            ('January', 'February', 'March', 'April', 'May', 'June',
             'July', 'August', 'September', 'October', 'November', 'December'),
            1,
        )}
        months.update({name[:3].lower(): index for name, index in months.items()})
        months.update({'sept': 9, 'sep': 9})
        try:
            month_key = month.lower().rstrip('.')
            return datetime(int(year), months[month_key], int(day)).date()
        except (KeyError, ValueError):
            return None

    match = re.search(r'\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b', value)
    if match:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
    match = re.search(r'\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b', value)
    if match:
        return datetime(int(match.group(3)), int(match.group(1)), int(match.group(2))).date()
    return None


def _source_url():
    return os.environ.get('BFAR_SHELLFISH_ADVISORY_URL', DEFAULT_BFAR_URL)


def _find_latest(html, source_url):
    """
    Find the latest BFAR shellfish/red tide advisory from HTML.
    Returns the advisory metadata without the bulletin number, which will be
    extracted from the PDF content during parsing.
    """
    parser = _BfarLinkParser()
    parser.feed(html)

    candidates = []

    # Terms that strongly indicate an actual advisory
    advisory_terms = (
        'shellfish advisory',
        'shellfish bulletin',
        'shellfish bulletin no',
        'red tide advisory',
        'red tide bulletin',
    )

    # Terms that indicate this is NOT an actual advisory
    excluded_terms = (
        'draft amendment',
        'fisheries administrative order',
        'administrative order',
        'faa',
        'memorandum',
        'memorandum circular',
        'consultation',
        'proposed',
        'rules and regulations',
    )
    for text, href in parser.links:
        absolute_url = urljoin(source_url, href)

        searchable = f'{text} {absolute_url}'.lower()
        searchable = re.sub(r'\s+', ' ', searchable).strip()

        # Must look like an actual advisory
        if not any(term in searchable for term in advisory_terms):
            continue

        # Ignore policy/draft/legal documents
        if any(term in searchable for term in excluded_terms):
            continue

        date = _parse_date(text) or _parse_date(absolute_url)

        candidates.append({
            'title': text.strip() or 'BFAR Shellfish Advisory',
            'source_url': absolute_url,
            'date': date,
        })

    if not candidates:
        raise ValueError(
            'No actual BFAR shellfish/red tide advisory was found '
            'on the BFAR source page.'
        )

    latest = max(
        candidates,
        key=lambda item: item['date'] or datetime.min.date()
    )

    logger.info(
        "Found latest BFAR advisory candidate: title=%s date=%s source=%s",
        latest['title'],
        latest['date'],
        latest['source_url'],
    )
    return latest


def _archive_pages(source_url):
    """Discover every official BFAR bulletin page through WordPress search."""
    pages = {}
    for page_number in range(1, 11):
        response = requests.get(
            WP_SEARCH_URL,
            params={'search': 'Shellfish Bulletin', 'per_page': 100, 'page': page_number},
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'SEASTEAM advisory sync/1.0'},
            verify=str(settings.BFAR_CA_BUNDLE),
        )
        if response.status_code == 400 or not response.content:
            break
        response.raise_for_status()
        results = response.json()
        if not results:
            break
        for result in results:
            title = result.get('title', '')
            url = result.get('url')
            if url and re.search(r'shellfish\s+bulletin', title, re.IGNORECASE):
                pages[url] = title
        if len(results) < 100:
            break
    return pages


def _table_candidates(html, page_url):
    parser = _BfarTableParser()
    parser.feed(html)
    candidates = []
    for row in parser.rows:
        values = [cell['value'] for cell in row['cells']]
        row_text = ' '.join(values)
        if not re.search(r'shellfish\s+bulletin|\bSB\b|bulletin\s+no', row_text, re.IGNORECASE):
            continue
        number = re.search(r'(?:shellfish\s+bulletin|bulletin|SB)\s*(?:no\.?\s*)?[#:.\- ]*(\d+)', row_text, re.IGNORECASE)
        links = [
            (text, urljoin(page_url, href))
            for cell in row['cells']
            for text, href in cell['links']
        ]
        pdf = next((url for _, url in links if url.lower().split('?', 1)[0].endswith('.pdf')), None)
        source = next((url for _, url in links if not url.lower().split('?', 1)[0].endswith('.pdf')), page_url)
        date = next((_parse_date(value) for value in values if _parse_date(value)), None)
        if number and pdf and date:
            candidate = {
                'title': row_text,
                'source_url': source,
                'pdf_url': pdf,
                'table_bulletin_number': f'SB {number.group(1)}',
                'table_date': date,
            }
            candidates.append(candidate)
            logger.info('Archive row parsed: %s on %s', candidate['table_bulletin_number'], date)
    logger.info('BFAR archive table rows discovered=%d valid=%d', len(parser.rows), len(candidates))
    return candidates


def _reconcile_candidate(candidate, parsed_data):
    """Resolve archive metadata against the actual PDF header."""
    pdf_number = parsed_data.get('bulletin_number')
    pdf_date = parsed_data.get('date')
    table_number = candidate.get('table_bulletin_number')
    table_date = candidate.get('table_date')
    metadata_number = candidate.get('metadata_bulletin_number')
    metadata_date = candidate.get('metadata_date')

    if table_number and pdf_number and table_number != pdf_number:
        logger.warning(
            'Rejected conflicting candidate source=%s pdf=%s table_number=%s pdf_number=%s',
            candidate['source_url'], candidate['pdf_url'], table_number, pdf_number,
        )
        return None, 'archive/PDF bulletin number conflict'

    if table_date and pdf_date and table_date != pdf_date:
        logger.warning(
            'Date conflict source=%s pdf=%s table_date=%s pdf_date=%s; using PDF header date',
            candidate['source_url'], candidate['pdf_url'], table_date, pdf_date,
        )

    bulletin_number = pdf_number or table_number or metadata_number
    advisory_date = pdf_date or table_date or metadata_date
    if not bulletin_number:
        return None, 'bulletin number is absent from both archive row and PDF header'
    if not advisory_date:
        return None, 'advisory date is absent from both archive row and PDF header'

    return {
        'bulletin_number': bulletin_number,
        'advisory_date': advisory_date,
        'bulletin_year': advisory_date.year,
        'verification_method': (
            'archive_verified' if table_number and table_date else
            'pdf_text_verified' if pdf_number or pdf_date else
            'metadata_verified'
        ),
    }, None


def _media_candidates(source_url):
    """Discover bulletin PDFs from every paginated BFAR media search result."""
    media = {}
    current_year = timezone.localdate().year
    searches = [
        {'search': term} for term in ('SB', 'Shellfish Bulletin', 'red tide')
    ]
    searches.append({
        'search': f'Shellfish Bulletin {current_year}',
    })
    for search in searches:
        for page_number in range(1, 101):
            response = requests.get(
                WP_MEDIA_URL,
                params={
                    **search,
                    'per_page': 100,
                    'page': page_number,
                },
                timeout=REQUEST_TIMEOUT,
                headers={'User-Agent': 'SEASTEAM advisory sync/1.0'},
                verify=str(settings.BFAR_CA_BUNDLE),
            )
            if response.status_code == 400 or not response.content:
                break
            response.raise_for_status()
            results = response.json()
            if not results:
                break
            for item in results:
                pdf_url = item.get('source_url', '')
                label = item.get('caption', {}).get('rendered', '')
                title = item.get('title', {}).get('rendered', '')
                searchable = f'{pdf_url} {label} {title}'
                if (
                    pdf_url.lower().split('?', 1)[0].endswith('.pdf')
                    and re.search(
                        r'(?:shellfish[-_ ]*bulletin|\bsb(?:[\s._:-]*no\.?)?[\s._:-]*\d+)',
                        searchable,
                        re.IGNORECASE,
                    )
                ):
                    media[pdf_url] = {
                        'title': title or 'BFAR Shellfish Bulletin',
                        'source_url': item.get('link') or pdf_url,
                        'pdf_url': pdf_url,
                    }
                    number_match = re.search(
                        r'(?:shellfish[-_ ]*bulletin|\bSB)[\s._:-]*(?:NO\.?\s*)?(\d+)',
                        searchable,
                        re.IGNORECASE,
                    )
                    media[pdf_url]['metadata_bulletin_number'] = (
                        f'SB {number_match.group(1)}' if number_match else None
                    )
                    media[pdf_url]['metadata_date'] = _parse_date(title) or _parse_date(pdf_url)
            total_pages = int(response.headers.get('X-WP-TotalPages', page_number))
            if page_number >= total_pages or len(results) < 100:
                break
    logger.info('BFAR media search discovered %d unique PDF links', len(media))
    return list(media.values())


def _archive_candidates(source_url):
    """Return bulletin pages and their official PDF links, newest discovery first."""
    candidates = []
    seen_pdfs = set()
    listing_urls = [source_url, urljoin(source_url, 'bulletins/')]
    for listing_url in listing_urls:
        try:
            response = requests.get(
                listing_url,
                timeout=REQUEST_TIMEOUT,
                headers={'User-Agent': 'SEASTEAM advisory sync/1.0'},
                verify=str(settings.BFAR_CA_BUNDLE),
            )
            response.raise_for_status()
            for candidate in _table_candidates(response.text, listing_url):
                if candidate['pdf_url'] not in seen_pdfs:
                    seen_pdfs.add(candidate['pdf_url'])
                    candidates.append(candidate)
            logger.info('Scanned BFAR archive URL: %s', listing_url)
        except requests.RequestException as exc:
            logger.warning('Could not scan BFAR archive URL %s: %s', listing_url, exc)
    for page_url, title in _archive_pages(source_url).items():
        response = requests.get(
            page_url,
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'SEASTEAM advisory sync/1.0'},
            verify=str(settings.BFAR_CA_BUNDLE),
        )
        response.raise_for_status()
        table_candidates = _table_candidates(response.text, page_url)
        for candidate in table_candidates:
            if candidate['pdf_url'] not in seen_pdfs:
                seen_pdfs.add(candidate['pdf_url'])
                candidates.append(candidate)
        parser = _BfarLinkParser()
        parser.feed(response.text)
        pdf_links = [
            urljoin(page_url, href)
            for text, href in parser.links
            if (
                urljoin(page_url, href).lower().split('?', 1)[0].endswith('.pdf')
                and re.search(
                    r'shellfish|bulletin|red[-_ ]?tide|\bsb[_\- ]?\d+',
                    f'{text} {href}',
                    re.IGNORECASE,
                )
            )
        ]
        if pdf_links and pdf_links[0] not in seen_pdfs:
            seen_pdfs.add(pdf_links[0])
            candidates.append({
                'title': title,
                'source_url': page_url,
                'pdf_url': pdf_links[0],
            })
    for candidate in _media_candidates(source_url):
        if candidate['pdf_url'] not in seen_pdfs:
            seen_pdfs.add(candidate['pdf_url'])
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            item.get('table_date')
            or item.get('metadata_date')
            or _parse_date(item.get('title', ''))
            or _parse_date(item.get('pdf_url', ''))
            or datetime.min.date()
        ),
        reverse=True,
    )
    logger.info('BFAR archive crawl discovered %d bulletin PDF candidates', len(candidates))
    return candidates


def fetch_pdf(advisory_dict):
    """
    Download PDF from advisory URL, validate it, calculate metadata,
    extract text using PyMuPDF, and parse the bulletin data.
    
    Args:
        advisory_dict: Dictionary with 'pdf_url' key and optionally 'pdf_filename'
        
    Returns:
        Tuple of (requests.Response, parsed_data_dict)
        Where parsed_data_dict contains:
        {
            'bulletin_number': str or None,
            'date': datetime.date or None,
            'affected_areas': list of str,
            'status': str,
            'important_information': str,
        }
        
    Raises:
        ValueError: If PDF validation or parsing fails
    """
    logger.info(f"Fetching PDF from {advisory_dict['pdf_url']}")
    
    response = requests.get(
        advisory_dict['pdf_url'],
        timeout=60,
        headers={
            'User-Agent': 'SEASTEAM advisory sync/1.0'
        },
        verify=str(settings.BFAR_CA_BUNDLE),
    )

    response.raise_for_status()
    content = response.content

    # Validate PDF signature
    if not content.startswith(b'%PDF'):
        raise ValueError('BFAR linked content is not a PDF.')

    # Calculate PDF metadata
    advisory_dict['pdf_content_type'] = (
        response.headers.get('Content-Type', 'application/pdf')[:120]
    )
    advisory_dict['pdf_size'] = len(content)
    advisory_dict['pdf_sha256'] = hashlib.sha256(content).hexdigest()
    advisory_dict['pdf_filename'] = (
        advisory_dict['pdf_url']
        .rsplit('/', 1)[-1]
        .split('?', 1)[0][:255]
    )

    logger.info(f"PDF downloaded: {advisory_dict['pdf_filename']} "
               f"({advisory_dict['pdf_size']} bytes, SHA256: {advisory_dict['pdf_sha256'][:8]}...)")

    # Parse PDF content to extract bulletin data
    try:
        parser = BfarPdfParser(pdf_filename=advisory_dict['pdf_filename'])
        parsed_data = parser.parse(content)
        parsed_data['pdf_text_extracted'] = bool(parser.pdf_text.strip())
        
        logger.info(f"PDF parsed successfully: Bulletin {parsed_data['bulletin_number']} "
                   f"dated {parsed_data['date']}")
        
        return response, parsed_data
        
    except Exception as exc:
        logger.error(f"PDF parsing failed: {exc}")
        raise ValueError(f"Failed to parse PDF content: {exc}") from exc



def sync_latest_advisory(force=False):
    """
    Synchronize the latest BFAR shellfish advisory.
    
    1. Fetches BFAR HTML page
    2. Finds the latest advisory
    3. Locates the associated PDF
    4. Downloads and parses the PDF
    5. Extracts bulletin data
    6. Stores in database (preserving historical bulletins)
    
    Returns dict with keys:
    - attempted: bool - whether sync was attempted
    - changed: bool - whether a new advisory was stored
    - cached: bool - whether cached data is available
    - error: str (optional) - error message if sync failed
    """
    latest = ShellfishAdvisory.objects.first()

    # Prevent overlapping syncs on SQLite, which otherwise raises database locked.
    if not _BFAR_SYNC_LOCK.acquire(blocking=False):
        logger.info('Skipping overlapping BFAR sync request; advisory sync is already running.')
        return {
            'attempted': False,
            'changed': False,
            'cached': bool(latest),
            'running': True,
            'error': 'BFAR advisory sync is already running. Please wait.',
        }

    try:
        # Check sync interval (unless forced)
        if latest and not force and timezone.now() - latest.last_checked_at < SYNC_INTERVAL:
            logger.debug('Skipping sync: within interval and not forced')
            return {
                'attempted': False,
                'changed': False,
                'cached': True,
                'running': False,
            }

        source_url = _source_url()
        logger.info(f"Fetching BFAR page: {source_url}")

        response = requests.get(
            source_url,
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'SEASTEAM advisory sync/1.0'},
            verify=str(settings.BFAR_CA_BUNDLE),
        )
        response.raise_for_status()

        try:
            latest_page_candidate = _find_latest(response.text, source_url)
        except ValueError:
            latest_page_candidate = None

        latest_pdf_url = latest_page_candidate['source_url'] if latest_page_candidate else ''
        if latest_page_candidate and latest_pdf_url.lower().split('?', 1)[0].endswith('.pdf'):
            candidates = [{
                **latest_page_candidate,
                'pdf_url': latest_pdf_url,
                'metadata_date': latest_page_candidate.get('date'),
            }]
            logger.info('Using direct latest BFAR homepage PDF: %s', latest_pdf_url)
        else:
            candidates = _archive_candidates(source_url)
        if not candidates:
            raise ValueError('No Shellfish Bulletin PDF was found in the BFAR archive.')

        created_count = 0
        updated_count = 0
        current_year_count = 0
        older_year_count = 0
        for candidate in candidates:
            try:
                _, parsed_data = fetch_pdf(candidate)
                identity, rejection_reason = _reconcile_candidate(candidate, parsed_data)
                if rejection_reason:
                    logger.warning(
                        'Skipped candidate source=%s pdf=%s reason=%s',
                        candidate['source_url'], candidate['pdf_url'], rejection_reason,
                    )
                    continue
                bulletin_number = identity['bulletin_number']
                advisory_date = identity['advisory_date']
                bulletin_year = identity['bulletin_year']
                candidate.update({
                    'bulletin_number': bulletin_number,
                    'advisory_date': advisory_date,
                    'bulletin_year': bulletin_year,
                    'status': parsed_data['status'],
                    'affected_areas': '\n'.join(parsed_data['affected_areas']),
                    'important_information': parsed_data['important_information'],
                    'pdf_text_extracted': parsed_data.get('pdf_text_extracted', False),
                    'verification_method': identity['verification_method'],
                })
                bulletin_obj, created = ShellfishAdvisory.objects.update_or_create(
                    bulletin_number=bulletin_number,
                    bulletin_year=bulletin_year,
                    defaults={
                        'title': candidate['title'],
                        'advisory_date': advisory_date,
                        'bulletin_year': bulletin_year,
                        'status': candidate['status'],
                        'affected_areas': candidate['affected_areas'],
                        'important_information': candidate['important_information'],
                        'source_url': candidate['source_url'],
                        'pdf_url': candidate['pdf_url'],
                        'pdf_filename': candidate['pdf_filename'],
                        'pdf_content_type': candidate['pdf_content_type'],
                        'pdf_size': candidate['pdf_size'],
                        'pdf_sha256': candidate['pdf_sha256'],
                        'pdf_text_extracted': candidate['pdf_text_extracted'],
                        'verification_method': candidate['verification_method'],
                        'pdf_checked_at': timezone.now(),
                    },
                )
                logger.info(
                    "Saved latest advisory record: bulletin=%s date=%s source=%s created=%s",
                    bulletin_number,
                    advisory_date,
                    candidate.get('source_url'),
                    created,
                )
                firebase_bulletins.upsert(candidate)
                created_count += int(created)
                updated_count += int(not created)
                if bulletin_year == timezone.now().year:
                    current_year_count += 1
                else:
                    older_year_count += 1
                break
            except (requests.RequestException, ValueError, OSError) as exc:
                logger.warning('Skipping invalid bulletin %s: %s', candidate['pdf_url'], exc)

        if created_count == 0 and updated_count == 0:
            raise ValueError('No valid Shellfish Bulletins could be parsed from the BFAR archive.')

        latest = ShellfishAdvisory.objects.first()
        current_year_numbers = set(
            ShellfishAdvisory.objects.filter(
                bulletin_year=timezone.localdate().year,
            ).values_list('bulletin_number', flat=True)
        )
        logger.info(
            'BFAR reconciliation discovered=%d parsed=%d current_year=%d older_year=%d '
            'saved_new=%d updated=%d current_year_numbers=%s',
            len(candidates), current_year_count + older_year_count,
            current_year_count, older_year_count, created_count, updated_count,
            ','.join(sorted(current_year_numbers)),
        )
        return {
            'attempted': True,
            'changed': bool(created_count),
            'created': created_count,
            'updated': updated_count,
            'discovered': len(candidates),
            'parsed': current_year_count + older_year_count,
            'current_year': current_year_count,
            'older_year': older_year_count,
            'cached': bool(latest),
            'running': False,
        }

    except (requests.RequestException, ValueError, OSError) as exc:
        logger.error(f"Sync failed: {exc}")

        if latest:
            ShellfishAdvisory.objects.filter(pk=latest.pk).update(last_checked_at=timezone.now())

        return {
            'attempted': True,
            'changed': False,
            'cached': bool(latest),
            'error': str(exc),
            'running': False,
        }
    finally:
        _BFAR_SYNC_LOCK.release()


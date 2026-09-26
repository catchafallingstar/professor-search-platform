"""OpenAlex access. HTTP lives in services/fetchers.py; this module re-exports it with types."""

import services.fetchers as fx;

def short_id(full: str) -> str {
    return fx.short_id(full) as str;
}

def clean_doi(doi: str) -> str {
    return fx.clean_doi(doi) as str;
}

def fetch_work_by_doi(doi: str) -> dict | None {
    return fx.fetch_work_by_doi(doi) as dict | None;
}

def search_work_by_title(title: str) -> list[dict] {
    return fx.search_work_by_title(title) as list[dict];
}

def search_authors(name: str, institution_id: str) -> list[dict] {
    return fx.search_authors(name, institution_id) as list[dict];
}

def fetch_author(author_id: str) -> dict | None {
    return fx.fetch_author(author_id) as dict | None;
}

def fetch_author_works(author_id: str, from_year: int) -> list[dict] {
    return fx.fetch_author_works(author_id, from_year) as list[dict];
}

def fetch_award(award_id: str) -> dict | None {
    return fx.fetch_award(award_id) as dict | None;
}

def search_institution(name: str, ror_id: str) -> dict | None {
    return fx.search_institution(name, ror_id) as dict | None;
}

def list_top_us_institutions(page: int) -> list[dict] {
    return fx.list_top_us_institutions(page) as list[dict];
}

def fetch_institution_awards(institution_id: str) -> list[dict] {
    return fx.fetch_institution_awards(institution_id) as list[dict];
}

"""Per-user history. Every endpoint is def:priv, so data lives on the caller's OWN root
and one user never sees another user's history."""

import from services.models { now_iso }

node SearchEntry {
    has query: str = "",
        university: str = "",
        department: str = "",
        subfield: str = "",
        has_grant: bool = False,
        has_hiring: bool = False,
        result_count: int = 0,
        created_at: str = "";
}

node ViewEntry {
    has professor_id: str = "",
        professor_name: str = "",
        university: str = "",
        department: str = "",
        viewed_at: str = "",
        view_count: int = 1;
}

obj SearchHistoryItem {
    has id: str = "",
        query: str = "",
        university: str = "",
        department: str = "",
        subfield: str = "",
        has_grant: bool = False,
        has_hiring: bool = False,
        result_count: int = 0,
        created_at: str = "";
}

obj ViewHistoryItem {
    has id: str = "",
        professor_id: str = "",
        professor_name: str = "",
        university: str = "",
        department: str = "",
        viewed_at: str = "",
        view_count: int = 1;
}

glob MAX_ITEMS: int = 200;

def:protect record_search(
    query: str = "",
    university: str = "",
    department: str = "",
    subfield: str = "",
    has_grant: bool = False,
    has_hiring: bool = False,
    result_count: int = 0
) -> bool {
    if not (query.strip() or university or department or subfield or has_grant or has_hiring) {
        return False;   # the default "show everything" load is not a search
    }
    entries = [root -->][?:SearchEntry];
    if len(entries) > 0 {
        last = entries[-1];
        # collapse keystroke-by-keystroke typing of the same search into one entry
        if last.university == university and last.department == department and last.subfield == subfield
            and last.has_grant == has_grant and last.has_hiring == has_hiring
            and (query.startswith(last.query) or last.query.startswith(query)) {
            last.query = query;
            last.result_count = result_count;
            last.created_at = now_iso();
            return True;
        }
    }
    root ++> SearchEntry(
        query=query.strip(), university=university, department=department, subfield=subfield,
        has_grant=has_grant, has_hiring=has_hiring, result_count=result_count, created_at=now_iso()
    );
    if len(entries) + 1 > MAX_ITEMS {
        oldest = entries[0];
        del oldest;
    }
    return True;
}

def:protect record_view(professor_id: str, professor_name: str, university: str = "", department: str = "") -> bool {
    for v in [root -->][?:ViewEntry] {
        if v.professor_id == professor_id {
            v.viewed_at = now_iso();
            v.view_count += 1;
            return True;
        }
    }
    root ++> ViewEntry(
        professor_id=professor_id, professor_name=professor_name, university=university,
        department=department, viewed_at=now_iso()
    );
    return True;
}

def:protect get_search_history() -> list[SearchHistoryItem] {
    items = [
        SearchHistoryItem(
            id=jid(s), query=s.query, university=s.university, department=s.department,
            subfield=s.subfield, has_grant=s.has_grant, has_hiring=s.has_hiring,
            result_count=s.result_count, created_at=s.created_at
        )
        for s in [root -->][?:SearchEntry]
    ];
    items.sort(key=lambda (i: SearchHistoryItem) -> str { return i.created_at; }, reverse=True);
    return items;
}

def:protect get_view_history() -> list[ViewHistoryItem] {
    items = [
        ViewHistoryItem(
            id=jid(v), professor_id=v.professor_id, professor_name=v.professor_name,
            university=v.university, department=v.department, viewed_at=v.viewed_at, view_count=v.view_count
        )
        for v in [root -->][?:ViewEntry]
    ];
    items.sort(key=lambda (i: ViewHistoryItem) -> str { return i.viewed_at; }, reverse=True);
    return items;
}

def:protect clear_search_history() -> int {
    entries = [root -->][?:SearchEntry];
    n = len(entries);
    for s in entries { del s; }
    return n;
}

def:protect clear_view_history() -> int {
    entries = [root -->][?:ViewEntry];
    n = len(entries);
    for v in entries { del v; }
    return n;
}

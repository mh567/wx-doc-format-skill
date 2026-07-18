from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

STYLE_ALIASES = {
    "body": ["正文", "Normal"],
    "title": ["文档标题"],
    "caption": ["Caption", "题注"],
    "table_body": ["表正文"],
    "note": ["3.1注-无编号注", "3.1 注-无编号注"],
    "numbered_note": ["3.2注-有编号注", "3.2 注-有编号注"],
    "formula": ["公式", "Normal"],
    "appendix_title": ["附录标题"],
    "appendix_heading_1": ["附录一级标题"],
    "appendix_heading_2": ["附录二级标题"],
    "appendix_heading_3": ["附录三级标题"],
    "list_letter": ["1.1一级列项-编号"],
    "list_dash": ["1.2一级列项-无编号"],
    "list_decimal": ["2.1二级列项-有编号"],
    "list_bullet": ["2.2二级列项-无编号"],
}


def w_attr(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def child(element, tag: str):
    return element.find(f"w:{tag}", NS) if element is not None else None


def value(element, attr: str = "val") -> str | None:
    return element.get(w_attr(attr)) if element is not None else None


def normalize_style_name(name: str) -> str:
    return name.casefold().replace(" ", "")


def read_style_profiles(styles_xml: bytes) -> dict[str, dict]:
    root = etree.fromstring(styles_xml)
    styles = {}
    by_key = {}
    for style in root.findall("w:style", NS):
        name = value(child(style, "name"))
        style_id = value(style, "styleId")
        if not name or not style_id:
            continue
        p_pr = child(style, "pPr")
        num_pr = child(p_pr, "numPr")
        r_pr = child(style, "rPr")
        r_fonts = child(r_pr, "rFonts") if r_pr is not None else None
        profile = {
            "style_id": style_id,
            "name": name,
            "type": value(style, "type"),
            "num_id": value(child(num_pr, "numId")) if num_pr is not None else None,
            "ilvl": value(child(num_pr, "ilvl")) if num_pr is not None else None,
            "outline": value(child(p_pr, "outlineLvl")) if p_pr is not None else None,
        }
        if r_pr is not None:
            sz = child(r_pr, "sz")
            sz_cs = child(r_pr, "szCs")
            b = child(r_pr, "b")
            profile["font_size_halftones"] = value(sz) if sz is not None else None
            profile["font_size_cs_halftones"] = value(sz_cs) if sz_cs is not None else None
            profile["bold"] = b is not None
        if r_fonts is not None:
            profile["font_east_asia"] = r_fonts.get("{" + W_NS + "}eastAsia")
            profile["font_ascii"] = r_fonts.get("{" + W_NS + "}ascii")
            profile["font_h_ansi"] = r_fonts.get("{" + W_NS + "}hAnsi")
        styles[name] = profile
        by_key[normalize_style_name(name)] = name
        by_key[normalize_style_name(style_id)] = name
    return {"styles": styles, "by_key": by_key}


def read_table_style_profile(styles: dict[str, dict], document_xml: bytes) -> dict:
    table_styles = [style for style in styles.values() if style.get("type") == "table"]
    selected = next(
        (style for style in table_styles if normalize_style_name(style["name"]) == "tablegrid"),
        next((style for style in table_styles if style.get("name") != "Normal Table"), None),
    )
    if not selected:
        return {}
    root = etree.fromstring(document_xml)
    tbl_look = {}
    for table in root.findall(".//w:tbl", NS):
        tbl_pr = child(table, "tblPr")
        if value(child(tbl_pr, "tblStyle")) != selected["style_id"]:
            continue
        look = child(tbl_pr, "tblLook")
        if look is not None:
            tbl_look = {
                etree.QName(key).localname: val
                for key, val in look.attrib.items()
            }
        break
    return {
        "name": selected["name"],
        "style_id": selected["style_id"],
        "tbl_look": tbl_look,
    }


def read_numbering_profile(numbering_xml: bytes) -> dict:
    root = etree.fromstring(numbering_xml)
    num_to_abstract = {}
    abstract_levels = {}
    for num in root.findall("w:num", NS):
        num_id = value(num, "numId")
        abstract_id = value(child(num, "abstractNumId"))
        if num_id and abstract_id:
            num_to_abstract[num_id] = abstract_id
    for abstract in root.findall("w:abstractNum", NS):
        abstract_id = value(abstract, "abstractNumId")
        if abstract_id is None:
            continue
        levels = []
        for level in abstract.findall("w:lvl", NS):
            levels.append(
                {
                    "ilvl": value(level, "ilvl"),
                    "numFmt": value(child(level, "numFmt")),
                    "lvlText": value(child(level, "lvlText")),
                    "pStyle": value(child(level, "pStyle")),
                    "start": value(child(level, "start")),
                }
            )
        abstract_levels[abstract_id] = levels
    return {"num_to_abstract": num_to_abstract, "abstract_levels": abstract_levels}


def resolve_style(profile: dict, role: str) -> str | None:
    by_key = profile.get("by_key", {})
    for candidate in STYLE_ALIASES.get(role, []):
        resolved = by_key.get(normalize_style_name(candidate))
        if resolved:
            return resolved
    return None


def style_numbering(profile: dict, style_name: str | None) -> dict | None:
    if not style_name:
        return None
    style = profile.get("styles", {}).get(style_name)
    if not style:
        return None
    num_id = style.get("num_id")
    if not num_id:
        return None
    abstract_id = profile.get("numbering", {}).get("num_to_abstract", {}).get(str(num_id))
    return {"num_id": int(num_id), "abstract_id": int(abstract_id) if abstract_id is not None else None, "ilvl": style.get("ilvl")}


def template_numbering_ids(profile: dict) -> dict:
    heading = style_numbering(profile, resolve_style(profile, "heading_1") or "heading 1")
    letter = style_numbering(profile, resolve_style(profile, "list_letter"))
    decimal = style_numbering(profile, resolve_style(profile, "list_decimal"))
    result = {}
    if heading:
        result["heading"] = heading["num_id"]
    if letter:
        result["list_letter"] = letter["num_id"]
        result["list_letter_abstract"] = letter["abstract_id"]
    if decimal:
        result["list_decimal"] = decimal["num_id"]
        result["list_decimal_abstract"] = decimal["abstract_id"]
    return {key: value for key, value in result.items() if value is not None}


def load_template_profile(template_path: Path | str) -> dict:
    template = Path(template_path)
    with ZipFile(template) as zf:
        styles_data = read_style_profiles(zf.read("word/styles.xml"))
        numbering_data = read_numbering_profile(zf.read("word/numbering.xml")) if "word/numbering.xml" in zf.namelist() else {}
        table_style = read_table_style_profile(styles_data["styles"], zf.read("word/document.xml"))
    profile = {
        "path": str(template),
        "styles": styles_data["styles"],
        "by_key": styles_data["by_key"],
        "numbering": numbering_data,
        "table_style": table_style,
        "resolved_styles": {},
        "missing_roles": [],
    }
    roles = list(STYLE_ALIASES)
    roles.extend([f"heading_{level}" for level in range(1, 7)])
    for role in roles:
        if role.startswith("heading_"):
            level = role.rsplit("_", 1)[1]
            candidates = [f"Heading {level}", f"heading {level}", f"标题 {level}"]
            resolved = None
            for candidate in candidates:
                resolved = profile["by_key"].get(normalize_style_name(candidate))
                if resolved:
                    break
        else:
            resolved = resolve_style(profile, role)
        if resolved:
            profile["resolved_styles"][role] = resolved
        else:
            profile["missing_roles"].append(role)
    profile["numbering_ids"] = template_numbering_ids(profile)
    return profile

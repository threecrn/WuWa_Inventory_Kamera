"""
wuwa_inventory_kamera.ui.inventory_models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Normalization helpers for read-only scan result viewing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .. import localization_data as _localization_data
from ..config.app_config import app_config, basePATH
from ..scraping import data as scraping_data

_SESSION_SCAN_RESULT = 'scan_result.json'
_SESSION_EXPORT_FILES: tuple[str, ...] = (
    'echoes_wuwainventorykamera.json',
    'weapons_wuwainventorykamera.json',
    'devItems_wuwainventorykamera.json',
    'resources_wuwainventorykamera.json',
    'characters_wuwainventorykamera.json',
)


@dataclass(frozen=True)
class InventoryRow:
    """UI-facing row rendered by the result viewer."""

    title: str
    subtitle: str = ''
    body_lines: tuple[str, ...] = field(default_factory=tuple)
    details_lines: tuple[str, ...] = field(default_factory=tuple)
    image_path: str | None = None


@dataclass(frozen=True)
class InventorySection:
    """A logical section inside one loaded result document."""

    title: str
    rows: tuple[InventoryRow, ...] = field(default_factory=tuple)


def filter_section_rows(section: InventorySection, query: str) -> InventorySection:
    """Return *section* with rows filtered by a case-insensitive text query."""
    normalized_query = query.strip().lower()
    if not normalized_query:
        return section

    rows = tuple(
        row
        for row in section.rows
        if _row_matches_query(row, normalized_query)
    )
    return InventorySection(title=section.title, rows=rows)


@dataclass(frozen=True)
class InventoryDocument:
    """Normalized result document consumed by the Inventory tab."""

    kind: str
    title: str
    sections: tuple[InventorySection, ...] = field(default_factory=tuple)
    message_lines: tuple[str, ...] = field(default_factory=tuple)


def _load_json_file(path: Path) -> object | None:
    return _localization_data.load_json_file(path)


def _resolve_game_language_code() -> str:
    return _localization_data.resolve_game_language_code(
        base_path=basePATH,
        selected_language=getattr(app_config, 'gameLanguage', 'English'),
    )


def _load_generated_catalog(filename: str) -> dict[str, dict]:
    return _localization_data.load_generated_catalog(filename, base_path=basePATH)


def _load_generated_locale(filename: str, language_code: str) -> dict[str, dict]:
    return _localization_data.load_generated_locale(
        filename,
        language_code,
        base_path=basePATH,
    )


class MetadataResolver:
    """Resolve ids into display labels and optional image paths."""

    def __init__(self) -> None:
        language_code = _resolve_game_language_code()

        item_mapping = scraping_data.getItemsID(language_code)
        self._items_by_id = self._build_info_lookup(item_mapping)
        self._items_by_key = self._build_info_key_lookup(item_mapping)
        generated_items_by_id, generated_items_by_key = self._build_generated_info_lookups(
            'items.json',
            language_code=language_code,
            fields=('image',),
        )
        self._items_by_id.update(generated_items_by_id)
        self._items_by_key.update(generated_items_by_key)

        weapon_mapping = scraping_data.getWeaponsID(language_code)
        self._weapons_by_id = self._build_info_lookup(weapon_mapping)
        self._weapons_by_key = self._build_info_key_lookup(weapon_mapping)
        generated_weapons_by_id, generated_weapons_by_key = self._build_generated_info_lookups(
            'weapons.json',
            language_code=language_code,
            fields=('image', 'rarity'),
        )
        self._weapons_by_id.update(generated_weapons_by_id)
        self._weapons_by_key.update(generated_weapons_by_key)

        character_mapping = scraping_data.getCharactersID(language_code)
        self._characters_by_id = self._build_name_lookup(character_mapping, prettify=True)
        self._characters_by_key = self._build_name_key_lookup(character_mapping, prettify=True)
        generated_characters_by_id, generated_characters_by_key = self._build_generated_name_lookups(
            'characters.json',
            language_code=language_code,
        )
        self._characters_by_id.update(generated_characters_by_id)
        self._characters_by_key.update(generated_characters_by_key)

        echo_mapping = scraping_data.getEchoesID(language_code)
        self._echoes_by_id = self._build_name_lookup(echo_mapping, prettify=True)
        self._echoes_by_key = self._build_name_key_lookup(echo_mapping, prettify=True)
        generated_echoes_by_id, generated_echoes_by_key = self._build_generated_name_lookups(
            'echoes.json',
            language_code=language_code,
        )
        self._echoes_by_id.update(generated_echoes_by_id)
        self._echoes_by_key.update(generated_echoes_by_key)

        achievement_mapping = scraping_data.getAchievementsID(language_code)
        self._achievements_by_id = self._build_name_lookup(achievement_mapping, prettify=False)
        self._achievements_by_key = self._build_name_key_lookup(achievement_mapping, prettify=False)
        generated_achievements_by_id, generated_achievements_by_key = self._build_generated_name_lookups(
            'achievements.json',
            language_code=language_code,
        )
        self._achievements_by_id.update(generated_achievements_by_id)
        self._achievements_by_key.update(generated_achievements_by_key)

        self._sonatas_by_key = self._build_generated_key_name_lookup(
            'sonatas.json',
            language_code=language_code,
        )

    @staticmethod
    def _build_info_lookup(mapping: dict) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for info in mapping.values():
            if isinstance(info, dict) and 'id' in info:
                result[str(info['id'])] = info
        return result

    @staticmethod
    def _build_info_key_lookup(mapping: dict) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for key, info in mapping.items():
            if isinstance(key, str) and key and isinstance(info, dict):
                result[key] = info
        return result

    @staticmethod
    def _build_name_lookup(mapping: dict, *, prettify: bool) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, identifier in mapping.items():
            if identifier is None:
                continue
            display_name = MetadataResolver._prettify_name(name) if prettify else str(name)
            result[str(identifier)] = display_name
        return result

    @staticmethod
    def _build_name_key_lookup(mapping: dict, *, prettify: bool) -> dict[str, str]:
        result: dict[str, str] = {}
        for name in mapping.keys():
            if not isinstance(name, str) or not name:
                continue
            result[name] = MetadataResolver._prettify_name(name) if prettify else str(name)
        return result

    @staticmethod
    def _extract_display_text(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get('display_name')
        if isinstance(value, str) and value:
            return value
        value = payload.get('display_text')
        if isinstance(value, str) and value:
            return value
        value = payload.get('name')
        if isinstance(value, str) and value:
            return value
        return None

    @classmethod
    def _build_generated_info_lookups(
        cls,
        catalog_filename: str,
        *,
        language_code: str,
        fields: tuple[str, ...],
    ) -> tuple[dict[str, dict], dict[str, dict]]:
        catalog = _load_generated_catalog(catalog_filename)
        locale = _load_generated_locale(catalog_filename, language_code)
        if not catalog or not locale:
            return {}, {}

        result_by_id: dict[str, dict] = {}
        result_by_key: dict[str, dict] = {}
        for canonical_key, info in catalog.items():
            if not isinstance(info, dict) or 'id' not in info:
                continue
            display_name = cls._extract_display_text(locale.get(canonical_key))
            if not display_name:
                continue

            record = {'name': display_name}
            for field in fields:
                value = info.get(field)
                if value is not None:
                    record[field] = value
            result_by_id[str(info['id'])] = record
            result_by_key[str(canonical_key)] = record
        return result_by_id, result_by_key

    @classmethod
    def _build_generated_name_lookups(
        cls,
        catalog_filename: str,
        *,
        language_code: str,
    ) -> tuple[dict[str, str], dict[str, str]]:
        catalog = _load_generated_catalog(catalog_filename)
        locale = _load_generated_locale(catalog_filename, language_code)
        if not catalog or not locale:
            return {}, {}

        result_by_id: dict[str, str] = {}
        result_by_key: dict[str, str] = {}
        for canonical_key, info in catalog.items():
            if not isinstance(info, dict) or 'id' not in info:
                continue
            display_name = cls._extract_display_text(locale.get(canonical_key))
            if not display_name:
                continue
            result_by_id[str(info['id'])] = display_name
            result_by_key[str(canonical_key)] = display_name
        return result_by_id, result_by_key

    @classmethod
    def _build_generated_key_name_lookup(cls, locale_filename: str, *, language_code: str) -> dict[str, str]:
        locale = _load_generated_locale(locale_filename, language_code)
        if not locale:
            return {}

        result: dict[str, str] = {}
        for canonical_key, record in locale.items():
            if not isinstance(canonical_key, str) or not canonical_key:
                continue
            display_name = cls._extract_display_text(record)
            if not display_name:
                continue
            result[canonical_key] = display_name
        return result

    @staticmethod
    def _prettify_name(raw_name: object) -> str:
        text = str(raw_name).replace('_', ' ').replace('-', ' ')
        if ' ' in text:
            return text.title()
        return text[:1].upper() + text[1:]

    @classmethod
    def _fallback_name(cls, raw_ref: str, prefix: str) -> str:
        if raw_ref and not raw_ref.isdigit():
            return cls._prettify_name(raw_ref)
        return f'{prefix} {raw_ref}'

    def resolve_item(self, item_id: object) -> tuple[str, str | None]:
        item_key = str(item_id)
        info = self._items_by_id.get(item_key) or self._items_by_key.get(item_key)
        if info:
            return str(info.get('name', item_key)), self._coerce_image(info.get('image'))
        return self._fallback_name(item_key, 'Item'), None

    def resolve_weapon(self, weapon_id: object) -> tuple[str, str | None, object | None]:
        weapon_key = str(weapon_id)
        info = self._weapons_by_id.get(weapon_key) or self._weapons_by_key.get(weapon_key)
        if info:
            return (
                str(info.get('name', weapon_key)),
                self._coerce_image(info.get('image')),
                info.get('rarity'),
            )
        return self._fallback_name(weapon_key, 'Weapon'), None, None

    def resolve_character(self, character_id: object) -> str:
        character_key = str(character_id)
        return self._characters_by_id.get(character_key) or self._characters_by_key.get(character_key) or self._fallback_name(character_key, 'Character')

    def resolve_echo(self, echo_id: object) -> str:
        echo_key = str(echo_id)
        return self._echoes_by_id.get(echo_key) or self._echoes_by_key.get(echo_key) or self._fallback_name(echo_key, 'Echo')

    def resolve_achievement(self, achievement_id: object) -> str:
        achievement_key = str(achievement_id)
        return (
            self._achievements_by_id.get(achievement_key)
            or self._achievements_by_key.get(achievement_key)
            or self._fallback_name(achievement_key, 'Achievement')
        )

    def resolve_sonata(self, sonata_key: object) -> str:
        sonata_ref = str(sonata_key)
        return self._sonatas_by_key.get(sonata_ref, self._fallback_name(sonata_ref, 'Sonata'))

    @staticmethod
    def _coerce_image(image_path: object) -> str | None:
        if isinstance(image_path, str) and image_path:
            return image_path
        return None


def _row_matches_query(row: InventoryRow, query: str) -> bool:
    searchable = ' '.join((row.title, row.subtitle, *row.body_lines, *row.details_lines)).lower()
    return query in searchable


def detect_document_kind(file_path: str, payload: object) -> str:
    """Infer the result document kind from file name and payload shape."""
    file_name = Path(file_path).name.lower()

    if file_name == 'scan_result.json' or _looks_like_scan_session(payload):
        return 'scan_session'
    if file_name.endswith('echoes_wuwainventorykamera.json') or _looks_like_echo_export(payload):
        return 'echoes_export'
    if file_name.endswith('weapons_wuwainventorykamera.json') or _looks_like_weapon_export(payload):
        return 'weapons_export'
    if (
        file_name.endswith('devitems_wuwainventorykamera.json')
        or file_name.endswith('resources_wuwainventorykamera.json')
        or _looks_like_items_export(payload)
    ):
        return 'items_export'
    if file_name.endswith('characters_wuwainventorykamera.json') or _looks_like_character_export(payload):
        return 'characters_export'
    if file_name.endswith('inventory_wuwainventorykamera.json') or _looks_like_legacy_inventory(payload):
        return 'unsupported_legacy'
    return 'unknown'


def load_inventory_document(file_path: str, payload: object) -> InventoryDocument:
    """Load and normalize a supported result document."""
    file_name = Path(file_path).name
    kind = detect_document_kind(file_path, payload)
    resolver = MetadataResolver()

    if kind == 'echoes_export':
        rows = _build_echo_rows(payload if isinstance(payload, list) else [], resolver)
        return _document_with_single_section(kind, file_name, 'Echoes', rows)
    if kind == 'weapons_export':
        rows = _build_weapon_rows(payload if isinstance(payload, list) else [], resolver)
        return _document_with_single_section(kind, file_name, 'Weapons', rows)
    if kind == 'items_export':
        section_title = _item_section_title(file_name)
        rows = _build_item_rows(payload if isinstance(payload, list) else [], resolver)
        return _document_with_single_section(kind, file_name, section_title, rows)
    if kind == 'characters_export':
        rows = _build_character_rows(payload if isinstance(payload, dict) else {}, resolver)
        return _document_with_single_section(kind, file_name, 'Characters', rows)
    if kind == 'scan_session':
        message_lines = _build_session_message_lines(payload if isinstance(payload, dict) else {})
        sections = _build_session_sections(payload if isinstance(payload, dict) else {}, resolver)
        if sections:
            return InventoryDocument(kind=kind, title=file_name, sections=sections, message_lines=message_lines)
        return InventoryDocument(
            kind=kind,
            title=file_name,
            message_lines=message_lines + ('No populated result sections were found in this session file.',),
        )
    if kind == 'unsupported_legacy':
        return InventoryDocument(
            kind=kind,
            title=file_name,
            message_lines=(
                'Legacy inventory files are no longer supported.',
                'Open scan_result.json or a current standalone export instead.',
            ),
        )
    return InventoryDocument(
        kind=kind,
        title=file_name,
        message_lines=(
            'This file is not a supported scan result format.',
            'Supported files: scan_result.json, echoes, weapons, devItems, resources, and characters exports.',
        ),
    )


def load_inventory_file(file_path: str | Path) -> InventoryDocument:
    """Read one result file from disk and normalize it."""
    path = Path(file_path)
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        return InventoryDocument(
            kind='error',
            title=path.name,
            message_lines=(
                'The selected file could not be parsed as JSON.',
                str(exc),
            ),
        )
    except OSError as exc:
        return InventoryDocument(
            kind='error',
            title=path.name,
            message_lines=(
                'The selected file could not be opened.',
                str(exc),
            ),
        )

    return load_inventory_document(str(path), payload)


def load_inventory_session(session_path: str | Path) -> InventoryDocument:
    """Load a session folder by preferring scan_result.json, then standalone exports."""
    folder = Path(session_path)
    folder_name = folder.name or str(folder)

    if not folder.exists() or not folder.is_dir():
        return InventoryDocument(
            kind='error',
            title=folder_name,
            message_lines=('The selected session folder does not exist.',),
        )

    scan_result_path = folder / _SESSION_SCAN_RESULT
    session_prefix = f'Session folder: {folder_name}'

    if scan_result_path.exists():
        document = load_inventory_file(scan_result_path)
        if document.kind != 'error':
            return _prepend_message_line(document, session_prefix)

    message_lines: list[str] = [session_prefix]
    if scan_result_path.exists():
        message_lines.extend(_prefix_message_lines(_SESSION_SCAN_RESULT, load_inventory_file(scan_result_path).message_lines))

    sections: list[InventorySection] = []
    for filename in _SESSION_EXPORT_FILES:
        file_path = folder / filename
        if not file_path.exists():
            continue

        document = load_inventory_file(file_path)
        if document.sections:
            sections.extend(document.sections)
            continue

        if document.message_lines and document.kind == 'error':
            message_lines.extend(_prefix_message_lines(filename, document.message_lines))

    if sections:
        return InventoryDocument(
            kind='scan_session',
            title=folder_name,
            sections=tuple(sections),
            message_lines=tuple(message_lines),
        )

    return InventoryDocument(
        kind='scan_session',
        title=folder_name,
        message_lines=tuple(message_lines + ['No supported result files were found in this session folder.']),
    )


def _document_with_single_section(
    kind: str,
    title: str,
    section_title: str,
    rows: tuple[InventoryRow, ...],
) -> InventoryDocument:
    if rows:
        return InventoryDocument(kind=kind, title=title, sections=(InventorySection(section_title, rows),))
    return InventoryDocument(
        kind=kind,
        title=title,
        message_lines=(f'{section_title} export is empty.',),
    )


def _prepend_message_line(document: InventoryDocument, message: str) -> InventoryDocument:
    return InventoryDocument(
        kind=document.kind,
        title=document.title,
        sections=document.sections,
        message_lines=(message,) + document.message_lines,
    )


def _prefix_message_lines(prefix: str, message_lines: tuple[str, ...]) -> list[str]:
    return [f'{prefix}: {line}' for line in message_lines]


def _build_session_sections(payload: dict, resolver: MetadataResolver) -> tuple[InventorySection, ...]:
    sections: list[InventorySection] = []

    echoes = payload.get('echoes')
    if isinstance(echoes, list):
        rows = _build_echo_rows(echoes, resolver)
        if rows:
            sections.append(InventorySection('Echoes', rows))

    weapons = payload.get('weapons')
    if isinstance(weapons, list):
        rows = _build_weapon_rows(weapons, resolver)
        if rows:
            sections.append(InventorySection('Weapons', rows))

    for key, title in (('devItems', 'Development Items'), ('resources', 'Resources')):
        data = payload.get(key)
        if isinstance(data, list):
            rows = _build_item_rows(data, resolver)
            if rows:
                sections.append(InventorySection(title, rows))

    characters = payload.get('characters')
    if isinstance(characters, dict):
        rows = _build_character_rows(characters, resolver)
        if rows:
            sections.append(InventorySection('Characters', rows))

    achievements = payload.get('achievements')
    if isinstance(achievements, list):
        rows = _build_achievement_rows(achievements, resolver)
        if rows:
            sections.append(InventorySection('Achievements', rows))

    shell = payload.get('shell')
    if isinstance(shell, dict):
        rows = _build_shell_rows(shell, resolver)
        if rows:
            sections.append(InventorySection('Shell', rows))

    return tuple(sections)


def _build_session_message_lines(payload: dict) -> tuple[str, ...]:
    lines: list[str] = []
    session_id = payload.get('date')
    if session_id:
        lines.append(f'Session: {session_id}')
    if 'cancelled' in payload:
        lines.append('Status: Cancelled' if payload.get('cancelled') else 'Status: Complete')
    return tuple(lines)


def _build_echo_rows(payload: list, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for entry in payload:
        if not isinstance(entry, dict) or len(entry) != 1:
            continue
        echo_id, details = next(iter(entry.items()))
        if not isinstance(details, dict):
            continue

        body_lines: list[str] = []
        summary = _join_non_empty_parts(
            _format_level(details.get('level')),
            _format_tune(details.get('tuneLv')),
            _format_rarity(details.get('rarity')),
        )
        if summary:
            body_lines.append(summary)

        sonata_ref = details.get('sonata_key') or details.get('sonata')
        if sonata_ref:
            body_lines.append(f'Sonata: {resolver.resolve_sonata(sonata_ref)}')

        cost = details.get('_cost')
        if cost is not None:
            body_lines.append(f'Cost: {cost}')

        equipped = details.get('_equipped')
        if equipped:
            body_lines.append(f'Equipped: {resolver.resolve_character(equipped)}')

        stats = details.get('stats')
        main_stat = _format_main_stat(stats)
        if main_stat:
            body_lines.append(main_stat)
        substat_count = _format_substat_count(stats)
        if substat_count:
            body_lines.append(substat_count)

        rows.append(
            InventoryRow(
                title=resolver.resolve_echo(echo_id),
                subtitle=(
                    f'Echo Key: {details.get("echo_key")}'
                    if details.get('echo_key')
                    else f'Echo ID: {echo_id}'
                ),
                body_lines=tuple(body_lines),
                details_lines=_build_echo_details(echo_id, details, resolver),
            )
        )
    return tuple(rows)


def _build_weapon_rows(payload: list, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for entry in payload:
        if not isinstance(entry, dict) or ('id' not in entry and 'weapon_key' not in entry):
            continue
        weapon_ref = entry.get('id', entry.get('weapon_key'))
        weapon_name, image_path, rarity = resolver.resolve_weapon(weapon_ref)

        body_lines: list[str] = []
        summary = _join_non_empty_parts(
            _format_level(entry.get('level')),
            _format_max_level(entry.get('maxLevel')),
            _format_rank(entry.get('rank')),
            _format_rarity(rarity),
        )
        if summary:
            body_lines.append(summary)

        equipped = entry.get('_equipped')
        if equipped:
            body_lines.append(f'Equipped: {resolver.resolve_character(equipped)}')

        rows.append(
            InventoryRow(
                title=weapon_name,
                subtitle=(
                    f'Weapon Key: {entry.get("weapon_key")}'
                    if entry.get('weapon_key')
                    else f'Weapon ID: {entry.get("id")}'
                ),
                body_lines=tuple(body_lines),
                details_lines=_build_weapon_details(entry, rarity),
                image_path=image_path,
            )
        )
    return tuple(rows)


def _build_item_rows(payload: list, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for entry in payload:
        if not isinstance(entry, dict) or ('id' not in entry and 'item_key' not in entry):
            continue
        item_ref = entry.get('id', entry.get('item_key'))
        item_name, image_path = resolver.resolve_item(item_ref)
        count = entry.get('count')
        body_lines = (f'Count: {count}',) if count is not None else ()
        rows.append(
            InventoryRow(
                title=item_name,
                subtitle=(
                    f'Item Key: {entry.get("item_key")}'
                    if entry.get('item_key')
                    else f'Item ID: {entry.get("id")}'
                ),
                body_lines=body_lines,
                details_lines=_build_item_details(entry),
                image_path=image_path,
            )
        )
    return tuple(rows)


def _build_character_rows(payload: dict, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for character_id, details in payload.items():
        if not isinstance(details, dict):
            continue

        body_lines: list[str] = []
        summary = _join_non_empty_parts(
            _format_level(details.get('level')),
            _format_ascension(details.get('ascension')),
            _format_chain(details.get('chain')),
        )
        if summary:
            body_lines.append(summary)

        weapon = details.get('weapon')
        if isinstance(weapon, dict) and weapon:
            weapon_ref = weapon.get('id', weapon.get('weapon_key', ''))
            weapon_name, _, _ = resolver.resolve_weapon(weapon_ref)
            weapon_summary = _join_non_empty_parts(
                weapon_name,
                _format_level(weapon.get('level')),
                _format_rank(weapon.get('rank')),
            )
            if weapon_summary:
                body_lines.append(f'Weapon: {weapon_summary}')

        skills = details.get('skills')
        if isinstance(skills, dict) and skills:
            body_lines.append(f'Skills: {len(skills)} entries')

        rows.append(
            InventoryRow(
                title=resolver.resolve_character(character_id),
                subtitle=(
                    f'Character Key: {details.get("character_key")}'
                    if details.get('character_key')
                    else f'Character ID: {character_id}'
                ),
                body_lines=tuple(body_lines),
                details_lines=_build_character_details(character_id, details, resolver),
            )
        )
    return tuple(rows)


def _build_achievement_rows(payload: list, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for achievement_id in payload:
        rows.append(
            InventoryRow(
                title=resolver.resolve_achievement(achievement_id),
                subtitle=f'Achievement ID: {achievement_id}',
                details_lines=(f'Achievement ID: {achievement_id}',),
            )
        )
    return tuple(rows)


def _build_shell_rows(payload: dict, resolver: MetadataResolver) -> tuple[InventoryRow, ...]:
    rows: list[InventoryRow] = []
    for item_id, amount in payload.items():
        item_name, image_path = resolver.resolve_item(item_id)
        rows.append(
            InventoryRow(
                title=item_name,
                subtitle=f'Item ID: {item_id}',
                body_lines=(f'Count: {amount}',),
                details_lines=(f'Item ID: {item_id}', f'Count: {amount}'),
                image_path=image_path,
            )
        )
    return tuple(rows)


def _build_echo_details(echo_id: object, details: dict, resolver: MetadataResolver) -> tuple[str, ...]:
    lines: list[str] = []

    echo_key = details.get('echo_key')
    if echo_key:
        lines.append(f'Echo Key: {echo_key}')
    lines.append(f'Echo ID: {echo_id}')

    for key, label in (
        ('level', 'Level'),
        ('tuneLv', 'Tune Level'),
        ('rarity', 'Rarity'),
        ('_cost', 'Cost'),
        ('_equipped', 'Equipped'),
        ('_scanIndex', 'Scan Index'),
        ('_monsterId', 'Monster ID'),
    ):
        value = details.get(key)
        if value is not None and value != '':
            if key == '_equipped':
                value = resolver.resolve_character(value)
            lines.append(f'{label}: {value}')

    sonata_key = details.get('sonata_key')
    if sonata_key:
        lines.append(f'Sonata Key: {sonata_key}')
    sonata_ref = sonata_key or details.get('sonata')
    if sonata_ref:
        lines.append(f'Sonata: {resolver.resolve_sonata(sonata_ref)}')

    stats = details.get('stats')
    if isinstance(stats, dict):
        main_stats = stats.get('main')
        if isinstance(main_stats, dict):
            for stat_name, stat_value in main_stats.items():
                lines.append(f'Main Stat: {stat_name} {stat_value}')

        sub_stats = stats.get('sub')
        if isinstance(sub_stats, dict):
            for stat_name, stat_value in sub_stats.items():
                lines.append(f'Substat: {stat_name} {stat_value}')

    return tuple(lines)


def _build_weapon_details(entry: dict, rarity: object) -> tuple[str, ...]:
    lines: list[str] = []

    if entry.get('weapon_key'):
        lines.append(f'Weapon Key: {entry.get("weapon_key")}')
    if entry.get('id') is not None:
        lines.append(f'Weapon ID: {entry.get("id")}')

    for label, value in (
        ('Level', entry.get('level')),
        ('Max Level', entry.get('maxLevel')),
        ('Rank', entry.get('rank')),
        ('Rarity', rarity),
        ('Equipped', entry.get('_equipped')),
    ):
        if value is not None and value != '':
            lines.append(f'{label}: {value}')

    return tuple(lines)


def _build_item_details(entry: dict) -> tuple[str, ...]:
    lines: list[str] = []
    if entry.get('item_key'):
        lines.append(f'Item Key: {entry.get("item_key")}')
    if entry.get('id') is not None:
        lines.append(f'Item ID: {entry.get("id")}')
    if 'count' in entry:
        lines.append(f'Count: {entry.get("count")}')
    return tuple(lines)


def _build_character_details(character_id: object, details: dict, resolver: MetadataResolver) -> tuple[str, ...]:
    lines: list[str] = []

    if details.get('character_key'):
        lines.append(f'Character Key: {details.get("character_key")}')
    lines.append(f'Character ID: {character_id}')

    for label, key in (
        ('Level', 'level'),
        ('Ascension', 'ascension'),
        ('Chain', 'chain'),
    ):
        value = details.get(key)
        if value is not None:
            lines.append(f'{label}: {value}')

    weapon = details.get('weapon')
    if isinstance(weapon, dict) and weapon:
        weapon_ref = weapon.get('id', weapon.get('weapon_key', ''))
        weapon_name, _, weapon_rarity = resolver.resolve_weapon(weapon_ref)
        lines.append(f'Weapon: {weapon_name}')
        if weapon.get('weapon_key'):
            lines.append(f'Weapon Key: {weapon.get("weapon_key")}')
        for label, value in (
            ('Weapon ID', weapon.get('id')),
            ('Weapon Level', weapon.get('level')),
            ('Weapon Ascension', weapon.get('ascension')),
            ('Weapon Rank', weapon.get('rank')),
            ('Weapon Rarity', weapon_rarity),
        ):
            if value is not None and value != '':
                lines.append(f'{label}: {value}')

    skills = details.get('skills')
    if isinstance(skills, dict):
        for skill_name, skill_level in skills.items():
            lines.append(f'Skill {skill_name}: {skill_level}')

    return tuple(lines)


def _item_section_title(file_name: str) -> str:
    normalized = file_name.lower()
    if 'devitems' in normalized:
        return 'Development Items'
    if 'resources' in normalized:
        return 'Resources'
    return 'Items'


def _looks_like_scan_session(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return any(key in payload for key in ('echoes', 'weapons', 'devItems', 'resources', 'characters', 'achievements', 'shell'))


def _looks_like_echo_export(payload: object) -> bool:
    if not isinstance(payload, list) or not payload:
        return False
    for entry in payload:
        if not isinstance(entry, dict) or len(entry) != 1:
            return False
        details = next(iter(entry.values()))
        if not isinstance(details, dict):
            return False
        if not any(key in details for key in ('level', 'tuneLv', 'rarity', 'stats', 'sonata')):
            return False
    return True


def _looks_like_weapon_export(payload: object) -> bool:
    if not isinstance(payload, list) or not payload:
        return False
    return all(
        isinstance(entry, dict)
        and 'id' in entry
        and any(key in entry for key in ('level', 'maxLevel', 'rank'))
        for entry in payload
    )


def _looks_like_items_export(payload: object) -> bool:
    if not isinstance(payload, list) or not payload:
        return False
    return all(isinstance(entry, dict) and 'id' in entry and 'count' in entry for entry in payload)


def _looks_like_character_export(payload: object) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    values = list(payload.values())
    return all(
        isinstance(entry, dict)
        and any(key in entry for key in ('level', 'ascension', 'weapon', 'skills', 'chain', '_name'))
        for entry in values
    )


def _looks_like_legacy_inventory(payload: object) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    return all(_is_int_like(value) for value in payload.values())


def _is_int_like(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        return value.isdigit()
    return False


def _join_non_empty_parts(*parts: str | None) -> str | None:
    present = [part for part in parts if part]
    if present:
        return ' | '.join(present)
    return None


def _format_level(value: object) -> str | None:
    if value is None:
        return None
    return f'Lv. {value}'


def _format_max_level(value: object) -> str | None:
    if value is None:
        return None
    return f'Max {value}'


def _format_tune(value: object) -> str | None:
    if value is None:
        return None
    return f'Tune {value}'


def _format_rank(value: object) -> str | None:
    if value is None:
        return None
    return f'Rank {value}'


def _format_rarity(value: object) -> str | None:
    if value is None:
        return None
    return f'Rarity {value}'


def _format_ascension(value: object) -> str | None:
    if value is None:
        return None
    return f'Ascension {value}'


def _format_chain(value: object) -> str | None:
    if value is None:
        return None
    return f'Chain {value}'


def _format_main_stat(stats: object) -> str | None:
    if not isinstance(stats, dict):
        return None
    main_stats = stats.get('main')
    if not isinstance(main_stats, dict) or not main_stats:
        return None
    stat_name, stat_value = next(iter(main_stats.items()))
    return f'Main: {stat_name} {stat_value}'


def _format_substat_count(stats: object) -> str | None:
    if not isinstance(stats, dict):
        return None
    sub_stats = stats.get('sub')
    if not isinstance(sub_stats, dict) or not sub_stats:
        return None
    return f'Substats: {len(sub_stats)}'
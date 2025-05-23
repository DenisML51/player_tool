# backend/app/crud/character.py
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import func, inspect as sqlainspect
from sqlalchemy.orm.properties import ColumnProperty # Импорт ColumnProperty
from fastapi import HTTPException, status
from typing import List, Optional, Tuple, Any # Добавлен Any

import random

from ..models.character import Character, CharacterInventoryItem
from ..models.item import Item, Weapon, Armor, Shield, GeneralItem, Ammo # Нужны для isinstance
from ..models.ability import Ability
from ..schemas.character import HealRequest
from ..models.status_effect import StatusEffect
from .utils import _parse_and_roll
from ..schemas.character import (
    CharacterCreate, CharacterBriefOut, CharacterDetailedOut, CharacterUpdateSkills,
    LevelUpInfo, UpdateCharacterStats, CharacterNotes, CharacterSkillModifiers, ShortRestRequest, ActiveAbilitySlotOut, AssignAbilitySlotRequest
)
from ..schemas.item import CharacterInventoryItemOut, WeaponOut, ArmorOut, ShieldOut, GeneralItemOut, AmmoOut, ItemBase
from ..schemas.ability import AbilityOut
from ..schemas.status_effect import StatusEffectOut
from .item import get_inventory_item, remove_item_from_inventory
# Импортируем утилиты
from .utils import (
    _get_skill_modifier,
    _calculate_initial_hp,
    _calculate_base_pu,
    _get_xp_for_level,
    _update_character_available_abilities,
    _calculate_total_ac,
    NEGATIVE_EMOTIONS, # Импортируем списки эмоций
    POSITIVE_EMOTIONS
)

from ..schemas import CustomItemOut
import logging

logger = logging.getLogger(__name__)
# Импортируем item CRUD для проверки экипировки при удалении
from . import item as item_crud
# Импортируем статус эффект CRUD для добавления эмоций
from . import reference as reference_crud


# --- Character Read Operations ---

def get_characters_by_user(db: Session, user_id: int) -> List[CharacterBriefOut]:
    """Получает список краткой информации о персонажах пользователя."""
    db_chars = db.query(Character).filter(Character.owner_id == user_id).all()
    # Используем Pydantic схему для формирования ответа
    return [
        CharacterBriefOut(
            id=c.id,
            name=c.name,
            level=c.level,
            current_hp=c.current_hp,
            max_hp=c.max_hp
        ) for c in db_chars
    ]

def get_character_details(db: Session, character_id: int, user_id: int) -> Optional[Character]:
    """
    Загружает персонажа со всеми необходимыми связями для детального отображения,
    включая инвентарь, экипировку и способности оружия.
    Проверяет принадлежность персонажа пользователю.
    """
    return db.query(Character).options(
        selectinload(Character.owner),
        # Инвентарь и предметы в нем
        selectinload(Character.inventory).selectinload(CharacterInventoryItem.item),
        # Экипировка (сразу грузим предметы, включая полиморфные способности оружия)
        selectinload(Character.equipped_armor).selectinload(CharacterInventoryItem.item),
        selectinload(Character.equipped_shield).selectinload(CharacterInventoryItem.item),
        selectinload(Character.equipped_weapon1)
            .selectinload(CharacterInventoryItem.item.of_type(Weapon)) # Явное указание типа для полиморфизма
            .selectinload(Weapon.granted_abilities), # Загрузка способностей оружия
        selectinload(Character.equipped_weapon2)
            .selectinload(CharacterInventoryItem.item.of_type(Weapon))
            .selectinload(Weapon.granted_abilities),
        # Изученные способности и активные состояния
        selectinload(Character.available_abilities),
        selectinload(Character.active_status_effects),
        selectinload(Character.custom_items)
    ).filter(
        Character.id == character_id,
        Character.owner_id == user_id # Проверка владельца
    ).first()


def get_character_details_for_output(db: Session, character_id: int, user_id: int) -> Optional[CharacterDetailedOut]:
    """Получает данные персонажа и формирует Pydantic схему CharacterDetailedOut для вывода."""
    db_char = get_character_details(db, character_id, user_id)
    if not db_char: return None

    total_ac = _calculate_total_ac(db_char)
    passive_attention = 10 + db_char.attention_mod
    next_level = db_char.level + 1
    xp_needed = _get_xp_for_level(next_level)

    character_data = {}
    char_mapper = sqlainspect(Character)

    # Копируем основные поля
    for prop in char_mapper.iterate_properties:
        if isinstance(prop, ColumnProperty) and prop.key in CharacterDetailedOut.model_fields:
            # Копируем флаги действий
            if prop.key in ['has_used_main_action', 'has_used_bonus_action', 'has_used_reaction']:
                character_data[prop.key] = getattr(db_char, prop.key, False)
            else:
                character_data[prop.key] = getattr(db_char, prop.key)

    # Формируем данные для слотов
    active_slots_data = {}
    for i in range(1, 6):
        slot_ability_attr = f"active_ability_{i}"
        slot_cooldown_attr = f"active_ability_slot_{i}_cooldown"
        slot_ability_obj = getattr(db_char, slot_ability_attr, None)
        slot_cooldown_val = getattr(db_char, slot_cooldown_attr, 0)
        ability_schema = None
        if slot_ability_obj:
            try: ability_schema = AbilityOut.from_orm(slot_ability_obj)
            except Exception as e: logger.error(f"Error creating AbilityOut schema for slot {i}: {e}")
        active_slots_data[f"active_slot_{i}"] = ActiveAbilitySlotOut(
            ability=ability_schema, cooldown_remaining=slot_cooldown_val
        )

    # Вспомогательная функция для инвентаря
    def get_inventory_item_schema(inv_item: Optional[CharacterInventoryItem]) -> Optional[CharacterInventoryItemOut]:
        if not inv_item or not inv_item.item: return None
        item_data = inv_item.item; item_schema: Any = None
        if isinstance(item_data, Weapon): item_schema = WeaponOut.from_orm(item_data)
        elif isinstance(item_data, Armor): item_schema = ArmorOut.from_orm(item_data)
        elif isinstance(item_data, Shield): item_schema = ShieldOut.from_orm(item_data)
        elif isinstance(item_data, GeneralItem): item_schema = GeneralItemOut.from_orm(item_data)
        elif isinstance(item_data, Ammo): item_schema = AmmoOut.from_orm(item_data)
        else: item_schema = ItemBase.from_orm(item_data)
        if item_schema is None: return None
        return CharacterInventoryItemOut(id=inv_item.id, item=item_schema, quantity=inv_item.quantity)

    inventory_list = [get_inventory_item_schema(inv_item) for inv_item in db_char.inventory if inv_item]
    inventory_list = [item for item in inventory_list if item is not None]
    custom_items_list = [CustomItemOut.from_orm(ci) for ci in db_char.custom_items]

    # Добавляем все данные в словарь
    character_data.update({
        "skill_modifiers": {f: getattr(db_char, f) for f in CharacterSkillModifiers.model_fields.keys() if hasattr(db_char, f)},
        "total_ac": total_ac, "passive_attention": passive_attention, "xp_needed_for_next_level": xp_needed,
        "max_hp": db_char.max_hp, "current_hp": db_char.current_hp, "base_pu": db_char.base_pu,
        "current_pu": db_char.current_pu, "stamina_points": db_char.stamina_points,
        "exhaustion_level": db_char.exhaustion_level, "speed": db_char.speed,
        "initiative_bonus": db_char.initiative_bonus, "base_ac": db_char.base_ac,
        "inventory": inventory_list,
        "equipped_armor": get_inventory_item_schema(db_char.equipped_armor),
        "equipped_shield": get_inventory_item_schema(db_char.equipped_shield),
        "equipped_weapon1": get_inventory_item_schema(db_char.equipped_weapon1),
        "equipped_weapon2": get_inventory_item_schema(db_char.equipped_weapon2),
        "available_abilities": [AbilityOut.from_orm(ab) for ab in db_char.available_abilities if ab],
        "active_status_effects": [StatusEffectOut.from_orm(se) for se in db_char.active_status_effects if se],
        "custom_items": custom_items_list,
        **active_slots_data # Добавляем слоты
    })

    # Создаем и возвращаем Pydantic модель
    try:
        result_schema = CharacterDetailedOut(**character_data)
        return result_schema
    except Exception as e:
        logger.error(f"Pydantic CharacterDetailedOut validation error: {e}", exc_info=True)
        return None


# --- Character Create / Update Operations ---

def create_character(db: Session, user_id: int, character_in: CharacterCreate) -> Character:
    """Создает нового персонажа с начальными характеристиками, навыками и уровнями веток."""
    print(f"\n--- CRUD: create_character (v4 - apply initial branches) ---")
    print(f"User ID: {user_id}, Input Data: {character_in.model_dump()}") # Используем model_dump() для Pydantic v2

    # Получаем данные для создания базового объекта
    char_data = character_in.model_dump(exclude={'initial_skills', 'initial_branch_levels'})
    skills_data = character_in.initial_skills.model_dump()
    char_data.update(skills_data) # Добавляем навыки

    # Рассчитываем начальные ПЗ, ПУ на основе характеристик и навыков
    # (Используем ваши функции из utils, предполагая, что они корректны)
    endurance_mod = _get_skill_modifier(skills_data.get('skill_endurance', 1))
    self_control_mod = skills_data.get('skill_self_control', 1)
    initial_max_hp = _calculate_initial_hp(endurance_mod) # Функция из utils
    initial_base_pu = _calculate_base_pu(self_control_mod) # Функция из utils

    # Создаем объект модели Character с начальными значениями
    db_char = Character(
        **char_data,
        owner_id=user_id,
        level=1, # Уровень всегда 1 при создании
        experience_points=0,
        max_hp=initial_max_hp,
        current_hp=initial_max_hp, # Начинаем с полным здоровьем
        base_pu=initial_base_pu,
        current_pu=initial_base_pu, # Начинаем с базовым ПУ
        stamina_points=max(1, 1), # Начальные ОС = Уровень 1
        exhaustion_level=0,
        # Уровни веток по умолчанию будут 0 из модели
    )
    print(f"  Initial calculated Max HP: {db_char.max_hp}, Base PU: {db_char.base_pu}, Stamina: {db_char.stamina_points}")

    # --- ПРИМЕНЯЕМ НАЧАЛЬНЫЕ УРОВНИ ВЕТОК ---
    # Схема CharacterCreate уже проверила, что initial_branch_levels передан и сумма уровней = 3
    if character_in.initial_branch_levels:
        print(f"  Applying initial branch levels: {character_in.initial_branch_levels}")
        for branch_key, level in character_in.initial_branch_levels.items():
            attribute_name = f"{branch_key}_branch_level"
            if hasattr(db_char, attribute_name):
                setattr(db_char, attribute_name, level)
                print(f"    Set {attribute_name} to {level}")
            else:
                # Этого не должно произойти, если VALID_BRANCH_KEYS в схеме верны
                print(f"    WARNING: Attribute {attribute_name} not found on Character model for key {branch_key}")
    else:
         # Этот случай не должен происходить из-за валидации в схеме
         print("  WARNING: Initial branch levels were not provided (schema validation might have failed or logic error).")
    # --- КОНЕЦ ПРИМЕНЕНИЯ УРОВНЕЙ ВЕТОК ---

    # Добавляем в сессию, коммитим и обновляем
    try:
        db.add(db_char)
        db.flush() # Получаем ID для связей
        db.refresh(db_char) # Обновляем объект из БД

        # Обновляем доступные способности на основе УЖЕ установленных уровней веток
        _update_character_available_abilities(db, db_char)

        db.commit() # Коммитим все изменения, включая добавленные способности
        db.refresh(db_char) # Обновляем еще раз, чтобы подтянуть связи способностей
        print(f"  Character created successfully with ID: {db_char.id}")
        return db_char
    except Exception as e:
        db.rollback()
        print(f"  ERROR: Commit failed during character creation: {e}")
        # Можно выбросить HTTPException или вернуть None/спец.ошибку
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при создании персонажа: {e}")




def update_character_skills(db: Session, character_id: int, user_id: int, skill_updates: CharacterUpdateSkills) -> Optional[Character]:
    """Обновляет базовые значения навыков персонажа (1-10)."""
    db_char = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not db_char:
        return None

    updated = False
    for skill_name, new_value in skill_updates.model_dump(exclude_unset=True).items():
        if new_value is not None and hasattr(db_char, skill_name):
            # Валидация диапазона (хотя Pydantic схема тоже должна валидировать)
            if not (1 <= new_value <= 10):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Навык {skill_name} должен быть между 1 и 10"
                )
            setattr(db_char, skill_name, new_value)
            updated = True

    if updated:
        # Пересчитываем базовую ПУ, если Самообладание изменилось
        db_char.base_pu = _calculate_base_pu(db_char.self_control_mod)
        # Пересчитать AC не нужно здесь, т.к. он зависит от Ловкости, брони и щита,
        # а не меняется напрямую при изменении других навыков через этот метод.
        db.commit()
        db.refresh(db_char)

    return db_char


def level_up_character(db: Session, character_id: int, user_id: int, level_up_data: LevelUpInfo) -> Optional[Character]:
    """Повышает уровень персонажа, обновляет статы, навыки и способности."""
    db_char = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not db_char:
        return None

    # Проверка на возможность повышения уровня по опыту
    xp_needed = _get_xp_for_level(db_char.level + 1)
    if xp_needed is None or db_char.experience_points < xp_needed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Недостаточно опыта для повышения уровня"
        )

    # Повышаем уровень
    db_char.level += 1

    # Обновляем Макс. ПЗ
    # Убедимся, что используем актуальный модификатор Выносливости
    current_endurance_mod = _get_skill_modifier(db_char.skill_endurance)
    db_char.max_hp += level_up_data.hp_roll + current_endurance_mod
    db_char.current_hp = db_char.max_hp

    # Повышаем уровень ветки
    branch_attr = f"{level_up_data.branch_point_spent}_branch_level"
    if hasattr(db_char, branch_attr):
        current_branch_level = getattr(db_char, branch_attr)
        if current_branch_level < 10: # Проверяем максимальный уровень ветки
            setattr(db_char, branch_attr, current_branch_level + 1)
        else:
             raise HTTPException(
                 status_code=status.HTTP_400_BAD_REQUEST,
                 detail=f"Ветка {level_up_data.branch_point_spent} уже достигла максимального уровня (10)"
             )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Неверное имя ветки класса: {level_up_data.branch_point_spent}"
        )

    # Распределяем очки навыков
    total_points_to_spend = 3
    spent_points = 0
    for skill_name, points_to_add in level_up_data.skill_points_spent.items():
        if points_to_add <= 0: continue # Пропускаем, если очков не добавляется

        if hasattr(db_char, skill_name):
            current_skill_level = getattr(db_char, skill_name)
            new_level = current_skill_level + points_to_add
            # Проверяем максимальный уровень навыка
            if new_level > 10:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Нельзя повысить навык {skill_name} выше 10"
                )
            setattr(db_char, skill_name, new_level)
            spent_points += points_to_add
        else:
            # Эта ошибка маловероятна, если фронтенд отправляет правильные ключи
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Неверное имя навыка: {skill_name}"
            )

    # Проверяем, что потрачено ровно нужное количество очков
    if spent_points != total_points_to_spend:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Должно быть распределено ровно {total_points_to_spend} очка навыка (потрачено: {spent_points})"
        )

    # Увеличиваем Очки Стойкости
    db_char.stamina_points += 1

    # Пересчитываем базовую ПУ (если Самообладание изменилось)
    new_self_control_mod = db_char.skill_self_control
    db_char.base_pu = min(new_self_control_mod, 8)

    # Обновляем доступные способности веток (после изменения уровня ветки)
    # Делаем это перед commit, чтобы изменения были атомарны
    _update_character_available_abilities(db, db_char)

    # Сохраняем все изменения
    db.commit()
    db.refresh(db_char) # Обновляем объект из БД
    return db_char

def update_character_stats(
    db: Session,
    character_id: int,
    user_id: int,
    stats_update: UpdateCharacterStats
) -> Tuple[Optional[Character], Optional[str]]:
    """Обновляет статы, проверяет триггеры ПУ и возвращает персонажа и имя сработавшей эмоции."""
    character = db.query(Character).options(
        selectinload(Character.active_status_effects) # Загружаем для добавления/проверки эмоций
    ).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not character:
        return None, None

    updated = False
    triggered_emotion_name: Optional[str] = None

    # --- Обновление других статов ---
    if stats_update.current_hp is not None:
        # Ограничиваем HP между 0 и максимумом
        character.current_hp = max(0, min(stats_update.current_hp, character.max_hp))
        updated = True
    if stats_update.stamina_points is not None:
        character.stamina_points = max(0, stats_update.stamina_points) # ОС не могут быть < 0
        updated = True
    if stats_update.exhaustion_level is not None:
        character.exhaustion_level = max(0, min(stats_update.exhaustion_level, 6)) # Истощение 0-6
        updated = True
    if stats_update.experience_points is not None:
        character.experience_points = max(0, stats_update.experience_points) # Опыт не может быть < 0
        updated = True

    # --- Обработка ПУ ---
    if stats_update.current_pu is not None:
        new_pu = stats_update.current_pu
        # Получаем результат проверки из схемы (может быть 'success', 'failure' или None)
        check_result = stats_update.check_result
        print(f"Updating PU for Char ID {character.id}: Current={character.current_pu}, Target={new_pu}, Base={character.base_pu}, Result='{check_result}'") # Отладка

        # 1. Ограничиваем новое значение ПУ (0-10, предполагаемый максимум)
        new_pu_clamped = max(0, min(new_pu, 10))

        # 2. Проверяем триггеры ДО изменения character.current_pu
        emotion_type_to_trigger: Optional[str] = None
        if new_pu_clamped <= 0 and check_result == 'failure':
            print(f"!!! Negative Emotion Triggered for Char ID {character.id}")
            emotion_type_to_trigger = 'negative'
        elif new_pu_clamped >= 10 and check_result == 'success':
            print(f"!!! Positive Emotion Triggered for Char ID {character.id}")
            emotion_type_to_trigger = 'positive'

        # 3. Применяем новое значение ПУ или сбрасываем, если триггер сработал
        if emotion_type_to_trigger:
            character.current_pu = character.base_pu # Сброс до базового
            print(f"    PU reset to base: {character.current_pu}")

            # 4. Определяем и пытаемся применить эмоцию
            roll = random.randint(1, 6)
            print(f"    d6 Roll for emotion: {roll}")
            emotion_name: Optional[str] = None
            if emotion_type_to_trigger == 'negative' and 1 <= roll <= len(NEGATIVE_EMOTIONS):
                emotion_name = NEGATIVE_EMOTIONS[roll - 1]
            elif emotion_type_to_trigger == 'positive' and 1 <= roll <= len(POSITIVE_EMOTIONS):
                emotion_name = POSITIVE_EMOTIONS[roll - 1]

            if emotion_name:
                print(f"    Selected Emotion: {emotion_name}")
                # Ищем эффект в БД по имени
                effect_to_apply = db.query(StatusEffect).filter(StatusEffect.name == emotion_name).first()
                if effect_to_apply:
                    # Используем функцию apply_status_effect этого же модуля
                    added_effect_name = apply_status_effect(db, character, effect_to_apply.id)
                    if added_effect_name:
                        triggered_emotion_name = added_effect_name # Сохраняем имя для возврата
                else:
                    print(f"    Warning: StatusEffect '{emotion_name}' not found in DB!")
            else:
                print(f"    Warning: Could not determine emotion for roll {roll} and type '{emotion_type_to_trigger}'")
        else:
            # Если триггер не сработал, просто обновляем ПУ до new_pu_clamped
            if character.current_pu != new_pu_clamped:
                character.current_pu = new_pu_clamped
                print(f"    PU updated to: {character.current_pu}")

        updated = True # Помечаем, что были изменения (даже если ПУ не изменилось, но был триггер)

    # --- Применяем изменения в БД, если они были ---
    if updated:
        try:
            db.commit()
            db.refresh(character) # Обновляем объект из БД
            # Дополнительно обновляем связи, которые могли измениться (статусы)
            # Это важно, если get_character_details_for_output вызывается *до* refresh
            # db.refresh(character, attribute_names=['active_status_effects']) # Попробуем без этого, т.к. get_character_details_for_output все равно запросит заново
            print(f"Character ID {character_id} stats updated. Triggered emotion: {triggered_emotion_name}")
        except Exception as e:
            db.rollback()
            print(f"Error during commit/refresh for char ID {character_id}: {e}")
            raise HTTPException(status_code=500, detail="Ошибка сохранения изменений персонажа")

    return character, triggered_emotion_name # Возвращаем кортеж


def update_character_notes(db: Session, character_id: int, user_id: int, notes_update: CharacterNotes) -> Optional[Character]:
    """Обновляет описательные заметки персонажа."""
    db_char = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not db_char:
        return None

    updated = False
    # Используем exclude_unset=True, чтобы обновлять только переданные поля
    for key, value in notes_update.model_dump(exclude_unset=True).items():
         if value is not None and hasattr(db_char, key):
              setattr(db_char, key, value)
              updated = True

    if updated:
        db.commit()
        db.refresh(db_char)

    return db_char


# --- Status Effects Operations (within character context) ---

def apply_status_effect(db: Session, character: Character, status_effect_id: int) -> Optional[str]:
    """
    Применяет статус-эффект к объекту персонажа (не коммитит).
    Возвращает имя эффекта, если он был добавлен, иначе None.
    """
    status_effect = db.query(StatusEffect).filter(StatusEffect.id == status_effect_id).first()
    if not status_effect:
        logger.warning(f"StatusEffect with ID {status_effect_id} not found.")
        return None

    # Используем множество ID для быстрой проверки
    current_effect_ids = {eff.id for eff in character.active_status_effects}

    if status_effect_id not in current_effect_ids:
        # Добавляем объект StatusEffect к списку связей персонажа
        # Убедимся, что объект status_effect находится в текущей сессии
        effect_in_session = db.merge(status_effect)
        character.active_status_effects.append(effect_in_session)
        # НЕ ДЕЛАЕМ COMMIT/FLUSH ЗДЕСЬ!
        logger.info(f"Effect '{status_effect.name}' (ID: {status_effect_id}) added to Character ID {character.id} session (pending commit)")
        return status_effect.name
    else:
        logger.info(f"Effect '{status_effect.name}' (ID: {status_effect_id}) already present on Character ID {character.id}")
        return None # Эффект уже был


def remove_status_effect(db: Session, character_id: int, user_id: int, status_effect_id: int) -> Optional[Character]:
    """Снимает статус-эффект с персонажа и коммитит изменения."""
    # Загружаем персонажа вместе с его активными эффектами
    character = db.query(Character).options(
        selectinload(Character.active_status_effects)
    ).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not character:
        return None # Персонаж не найден или не принадлежит пользователю

    # Находим сам объект StatusEffect по ID (для удаления из списка связей)
    status_effect_to_remove = None
    for effect in character.active_status_effects:
        if effect.id == status_effect_id:
            status_effect_to_remove = effect
            break

    if status_effect_to_remove:
        character.active_status_effects.remove(status_effect_to_remove)
        try:
            db.commit()
            db.refresh(character) # Обновляем объект после коммита
            print(f"Статус-эффект ID {status_effect_id} удален у персонажа ID {character_id}")
            return character
        except Exception as e:
            db.rollback()
            print(f"Ошибка commit при удалении статуса ID {status_effect_id} у персонажа ID {character_id}: {e}")
            # Можно пробросить ошибку или вернуть None
            return None
    else:
        # Эффект не был найден у персонажа
        print(f"Статус-эффект ID {status_effect_id} не найден у персонажа ID {character_id}")
        # Возвращаем None или можно вернуть character без изменений, если нужно
        return None
    

def heal_character(db: Session, character_id: int, user_id: int, heal_request: HealRequest) -> Optional[Character]:
    """
    Обрабатывает запрос на лечение, включая потребление зарядов/количества аптечки,
    ИСПОЛЬЗУЯ ФОРМУЛУ ИЗ ПРЕДМЕТА.
    """
    print(f"\n--- CRUD: heal_character (v4 - with formula) ---") # Обновили версию в логе
    print(f"Character ID: {character_id}, User ID: {user_id}, Request: {heal_request}")

    # <<< ИЗМЕНЕНИЕ НАЧАЛО: Загружаем персонажа с модификаторами >>>
    # Вместо простого запроса, загрузим сразу или получим модификаторы позже
    character = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()
    # <<< ИЗМЕНЕНИЕ КОНЕЦ >>>

    if not character:
        print(f"  ERROR: Character {character_id} not found or doesn't belong to user {user_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден или не принадлежит вам")

    if character.current_hp >= character.max_hp:
        print(f"  INFO: Character {character_id} already at max HP.")
        # Возвращаем персонажа без изменений, т.к. ошибки нет, но и действия тоже
        return character # <<< ИЗМЕНЕНИЕ: Возвращаем персонажа вместо ошибки 400

    healing_amount = 0
    roll_details_str = "Нет броска" # Для логов
    source = heal_request.source
    resource_consumed = False # Флаг, что ресурс был потрачен

    if source == 'medkit':
        inventory_item_id = heal_request.inventory_item_id
        if not inventory_item_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Не указан ID предмета инвентаря для лечения аптечкой")

        print(f"  Healing source: Medkit (Inventory Item ID: {inventory_item_id})")
        inv_item = get_inventory_item(db, inventory_item_id, character_id, user_id)
        if not inv_item:
            print(f"  ERROR: Inventory item {inventory_item_id} not found for character {character_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Указанный предмет не найден в инвентаре")

        # Проверяем, что это расходник (GeneralItem) и есть заряды/количество
        if not isinstance(inv_item.item, GeneralItem): # Можно уточнить категорию "Медицина"
             print(f"  ERROR: Item '{inv_item.item.name}' is not a GeneralItem.")
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Предмет '{inv_item.item.name}' не является расходником.")

        if inv_item.quantity <= 0:
            print(f"  ERROR: Item '{inv_item.item.name}' (Inv ID: {inv_item.id}) has no uses left (quantity: {inv_item.quantity}).")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"У предмета '{inv_item.item.name}' закончились использования.")

        # <<< ИЗМЕНЕНИЕ НАЧАЛО: Расчет лечения по формуле >>>
        item_formula = getattr(inv_item.item, 'effect_dice_formula', None)
        if item_formula:
            try:
                # Используем новую функцию из utils
                healing_amount, roll_details_str = _parse_and_roll(item_formula, character)
                print(f"  Calculated Heal (from formula '{item_formula}'): {healing_amount}. Roll details: {roll_details_str}")
            except Exception as e:
                print(f"  ERROR: Failed to parse or roll formula '{item_formula}': {e}")
                # Что делать при ошибке? Можно кинуть 500 или лечить на 0
                healing_amount = 0
                roll_details_str = f"Ошибка формулы: {e}"
                # Можно добавить HTTPException, если это критично
                # raise HTTPException(status_code=500, detail=f"Ошибка расчета формулы лечения: {e}")
        else:
            # Обработка случая, если у предмета нет формулы (лечит на 0 или ошибка?)
            print(f"  WARNING: No effect_dice_formula found for item '{inv_item.item.name}'. Healing amount is 0.")
            healing_amount = 0
            roll_details_str = "Нет формулы"
        # <<< ИЗМЕНЕНИЕ КОНЕЦ >>>

        # Уменьшаем количество ИЛИ удаляем предмет (логика без изменений)
        if inv_item.quantity > 1:
            inv_item.quantity -= 1
            print(f"  Decremented quantity for Inv ID {inv_item.id}. New quantity: {inv_item.quantity}")
            resource_consumed = True
        else:
            print(f"  Last use for Inv ID {inv_item.id}. Deleting item.")
            # Сначала снимаем, если экипировано (хотя аптечки не должны быть)
            if character.armor_inv_item_id == inv_item.id: character.armor_inv_item_id = None
            if character.shield_inv_item_id == inv_item.id: character.shield_inv_item_id = None
            if character.weapon1_inv_item_id == inv_item.id: character.weapon1_inv_item_id = None
            if character.weapon2_inv_item_id == inv_item.id: character.weapon2_inv_item_id = None
            # Затем удаляем
            db.delete(inv_item)
            resource_consumed = True
            print(f"  Deleted Inv ID {inv_item.id}.")

    elif source == 'short_rest_die':
        dice_count = heal_request.dice_count or 1
        print(f"  Healing source: Short Rest Dice. Count: {dice_count}")
        if character.stamina_points < dice_count:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Недостаточно Очков Стойкости (ОС)")

        # <<< ИЗМЕНЕНИЕ НАЧАЛО: Используем _parse_and_roll для Короткого Отдыха >>>
        # Формула Короткого Отдыха: 1к10+Мод.Вын за каждый кубик ОС
        short_rest_formula = "1к10+Мод.Вын"
        total_healing_roll = 0
        all_roll_details = []
        for i in range(dice_count):
            try:
                roll_result, details = _parse_and_roll(short_rest_formula, character)
                total_healing_roll += roll_result
                all_roll_details.append(f"ОС{i+1}: {details}")
            except Exception as e:
                 print(f"  ERROR: Failed to parse or roll formula '{short_rest_formula}' for stamina die {i+1}: {e}")
                 all_roll_details.append(f"ОС{i+1}: Ошибка формулы")
                 # Можно добавить обработку ошибки, если нужно

        healing_amount = total_healing_roll
        roll_details_str = " | ".join(all_roll_details)
        print(f"  Spent {dice_count} Stamina Dice. Total Heal: {healing_amount}. Details: {roll_details_str}")
        # <<< ИЗМЕНЕНИЕ КОНЕЦ >>>

        character.stamina_points -= dice_count
        print(f"  Stamina points remaining: {character.stamina_points}")
        resource_consumed = True
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Неизвестный источник лечения: {source}")

    # --- Применяем лечение ---
    # Лечение не может быть отрицательным
    effective_healing = max(0, healing_amount)
    new_hp = min(character.max_hp, character.current_hp + effective_healing)
    healed_for = new_hp - character.current_hp # Фактическое изменение HP

    print(f"  Applied Healing: {healed_for} (Calculated: {healing_amount}). New HP: {new_hp}/{character.max_hp}")

    # Сохраняем, если было фактическое лечение ИЛИ потрачен ресурс
    if healed_for > 0 or resource_consumed:
        character.current_hp = new_hp
        try:
            db.commit() # Сохраняем изменения HP, ОС и/или удаление/изменение quantity предмета
            db.refresh(character) # Обновляем объект персонажа
            print(f"  Successfully applied healing/cost and committed.")
            return character
        except Exception as e:
            db.rollback()
            print(f"  ERROR: Commit failed after applying healing/cost: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка базы данных при сохранении лечения: {e}")
    else:
        print(f"  No actual healing occurred and no resources consumed.")
        # Возвращаем персонажа без изменений, т.к. коммита не было
        return character

def perform_short_rest(db: Session, character_id: int, user_id: int, request: ShortRestRequest) -> Optional[Character]:
    """Выполняет короткий отдых: тратит ОС на лечение ПЗ и восстанавливает ПУ."""
    print(f"\n--- CRUD: perform_short_rest ---")
    print(f"Character ID: {character_id}, User ID: {user_id}, Request: {request}")

    character = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not character:
        print(f"  ERROR: Character {character_id} not found or doesn't belong to user {user_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден")

    dice_to_spend = request.dice_to_spend
    if character.stamina_points < dice_to_spend:
        print(f"  ERROR: Not enough Stamina Points. Has {character.stamina_points}, needs {dice_to_spend}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Недостаточно Очков Стойкости (ОС). Доступно: {character.stamina_points}")

    # 1. Тратим ОС
    character.stamina_points -= dice_to_spend
    print(f"  Spent {dice_to_spend} Stamina Dice. Remaining: {character.stamina_points}")

    # 2. Лечим ПЗ
    endurance_mod = character.endurance_mod
    hp_healed = 0
    for _ in range(dice_to_spend):
        roll = random.randint(1, 10)
        hp_healed += max(0, roll + endurance_mod) # Лечение не может быть отрицательным
    new_hp = min(character.max_hp, character.current_hp + hp_healed)
    actual_hp_gain = new_hp - character.current_hp
    character.current_hp = new_hp
    print(f"  HP Healed: {actual_hp_gain} (Rolled {dice_to_spend}d10 + {dice_to_spend}*Mod[{endurance_mod}] = {hp_healed}). New HP: {character.current_hp}/{character.max_hp}")

    # 3. Восстанавливаем ПУ
    pu_roll = random.randint(1, 4)
    # Восстанавливаем до базового значения, но не выше
    new_pu = min(character.base_pu, character.current_pu + pu_roll)
    actual_pu_gain = new_pu - character.current_pu
    character.current_pu = new_pu
    print(f"  PU Recovered: {actual_pu_gain} (Rolled 1d4: {pu_roll}). New PU: {character.current_pu}/{character.base_pu}")

    # 4. Сохраняем
    try:
        db.commit()
        db.refresh(character)
        print(f"  Short rest completed and committed.")
        return character
    except Exception as e:
        db.rollback()
        print(f"  ERROR: Commit failed during short rest: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при коротком отдыхе: {e}")


def perform_long_rest(db: Session, character_id: int, user_id: int) -> Optional[Character]:
    """Выполняет длительный отдых: восстанавливает ПЗ, ОС, ПУ, снижает Истощение."""
    print(f"\n--- CRUD: perform_long_rest ---")
    print(f"Character ID: {character_id}, User ID: {user_id}")

    character = db.query(Character).filter(
        Character.id == character_id,
        Character.owner_id == user_id
    ).first()

    if not character:
        print(f"  ERROR: Character {character_id} not found or doesn't belong to user {user_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Персонаж не найден")

    # 1. Восстанавливаем ПЗ
    character.current_hp = character.max_hp
    print(f"  HP restored to max: {character.max_hp}")

    # 2. Восстанавливаем ОС
    # Считаем максимум ОС = Уровень + Мод.Выносливости (минимум 1)
    endurance_mod = character.endurance_mod
    max_stamina_points = max(1, character.level)
    character.stamina_points = max_stamina_points
    print(f"  Stamina Points restored to max: {max_stamina_points} (Level {character.level} + EndMod {endurance_mod})")

    # 3. Снижаем Истощение
    if character.exhaustion_level > 0:
        character.exhaustion_level -= 1
        print(f"  Exhaustion level reduced by 1. New level: {character.exhaustion_level}")

    # 4. Сбрасываем ПУ до базового
    character.current_pu = character.base_pu
    print(f"  PU reset to base: {character.base_pu}")

    # TODO: Сброс использований способностей (если будет реализовано отслеживание)

    # 5. Сохраняем
    try:
        db.commit()
        db.refresh(character)
        print(f"  Long rest completed and committed.")
        return character
    except Exception as e:
        db.rollback()
        print(f"  ERROR: Commit failed during long rest: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка базы данных при длительном отдыхе: {e}")

# backend/app/models/character.py
from __future__ import annotations
from typing import Optional, List
from sqlalchemy import Column, Integer, String, ForeignKey, Table, Text, Boolean # <-- Добавили Boolean
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.ext.hybrid import hybrid_property
import math # Нужен для hybrid_property
from ..db.database import Base
# Импортируем таблицы связей
from .association_tables import character_abilities, character_status_effects
# Импортируем зависимые модели (или используем строки)
from .user import User
from .item import Item # Нужен для связи с CharacterInventoryItem
from .custom_item import CharacterCustomItem
from .ability import Ability # Добавим импорт Ability

# Таблица опыта
XP_THRESHOLDS = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500,
    6: 14000, 7: 23000, 8: 34000, 9: 48000, 10: 64000,
}

# --- Модель Предмета Инвентаря ---
class CharacterInventoryItem(Base):
    __tablename__ = 'character_inventory_items'
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey('characters.id'))
    item_id: Mapped[int] = mapped_column(ForeignKey('items.id'))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    item: Mapped["Item"] = relationship(lazy="joined")

# --- Основная модель Персонажа ---
class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    level: Mapped[int] = mapped_column(Integer, default=1)
    experience_points: Mapped[int] = mapped_column(Integer, default=0)

    # --- Навыки (18 штук) ---
    skill_strength: Mapped[int] = mapped_column(Integer, default=1)
    skill_dexterity: Mapped[int] = mapped_column(Integer, default=1)
    skill_endurance: Mapped[int] = mapped_column(Integer, default=1)
    skill_reaction: Mapped[int] = mapped_column(Integer, default=1)
    skill_technique: Mapped[int] = mapped_column(Integer, default=1)
    skill_adaptation: Mapped[int] = mapped_column(Integer, default=1)
    skill_logic: Mapped[int] = mapped_column(Integer, default=1)
    skill_attention: Mapped[int] = mapped_column(Integer, default=1)
    skill_erudition: Mapped[int] = mapped_column(Integer, default=1)
    skill_culture: Mapped[int] = mapped_column(Integer, default=1)
    skill_science: Mapped[int] = mapped_column(Integer, default=1)
    skill_medicine: Mapped[int] = mapped_column(Integer, default=1)
    skill_suggestion: Mapped[int] = mapped_column(Integer, default=1)
    skill_insight: Mapped[int] = mapped_column(Integer, default=1)
    skill_authority: Mapped[int] = mapped_column(Integer, default=1)
    skill_self_control: Mapped[int] = mapped_column(Integer, default=1)
    skill_religion: Mapped[int] = mapped_column(Integer, default=1)
    skill_flow: Mapped[int] = mapped_column(Integer, default=1)

    # --- Модификаторы (рассчитываются на лету) ---
    def _get_modifier(self, skill_value: int) -> int:
        if skill_value <= 1: return 0
        if skill_value <= 3: return 1
        if skill_value <= 5: return 2
        if skill_value <= 7: return 3
        if skill_value <= 9: return 4
        return 5 # Для 10

    @hybrid_property
    def strength_mod(self): return self._get_modifier(self.skill_strength)
    @hybrid_property
    def dexterity_mod(self): return self._get_modifier(self.skill_dexterity)
    @hybrid_property
    def endurance_mod(self): return self._get_modifier(self.skill_endurance)
    @hybrid_property
    def reaction_mod(self): return self._get_modifier(self.skill_reaction)
    @hybrid_property
    def technique_mod(self): return self._get_modifier(self.skill_technique)
    @hybrid_property
    def adaptation_mod(self): return self._get_modifier(self.skill_adaptation)
    @hybrid_property
    def logic_mod(self): return self._get_modifier(self.skill_logic)
    @hybrid_property
    def attention_mod(self): return self._get_modifier(self.skill_attention)
    @hybrid_property
    def erudition_mod(self): return self._get_modifier(self.skill_erudition)
    @hybrid_property
    def culture_mod(self): return self._get_modifier(self.skill_culture)
    @hybrid_property
    def science_mod(self): return self._get_modifier(self.skill_science)
    @hybrid_property
    def medicine_mod(self): return self._get_modifier(self.skill_medicine)
    @hybrid_property
    def suggestion_mod(self): return self._get_modifier(self.skill_suggestion)
    @hybrid_property
    def insight_mod(self): return self._get_modifier(self.skill_insight)
    @hybrid_property
    def authority_mod(self): return self._get_modifier(self.skill_authority)
    @hybrid_property
    def self_control_mod(self): return self._get_modifier(self.skill_self_control)
    @hybrid_property
    def religion_mod(self): return self._get_modifier(self.skill_religion)
    @hybrid_property
    def flow_mod(self): return self._get_modifier(self.skill_flow)

    # --- Производные характеристики ---
    max_hp: Mapped[int] = mapped_column(Integer, default=10)
    current_hp: Mapped[int] = mapped_column(Integer, default=10)
    base_pu: Mapped[int] = mapped_column(Integer, default=1)
    current_pu: Mapped[int] = mapped_column(Integer, default=1)
    stamina_points: Mapped[int] = mapped_column(Integer, default=1)
    exhaustion_level: Mapped[int] = mapped_column(Integer, default=0)
    speed: Mapped[int] = mapped_column(Integer, default=10)

    @hybrid_property
    def initiative_bonus(self) -> int: return self.reaction_mod
    @hybrid_property
    def base_ac(self) -> int: return 10 + self.dexterity_mod

    # --- Уровни веток ---
    medic_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    mutant_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    sharpshooter_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    scout_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    technician_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    fighter_branch_level: Mapped[int] = mapped_column(Integer, default=0)
    juggernaut_branch_level: Mapped[int] = mapped_column(Integer, default=0)

    # --- Снаряжение и инвентарь ---
    armor_inv_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey('character_inventory_items.id'), nullable=True)
    shield_inv_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey('character_inventory_items.id'), nullable=True)
    weapon1_inv_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey('character_inventory_items.id'), nullable=True)
    weapon2_inv_item_id: Mapped[Optional[int]] = mapped_column(ForeignKey('character_inventory_items.id'), nullable=True)

    inventory: Mapped[List["CharacterInventoryItem"]] = relationship(
        cascade="all, delete-orphan",
        foreign_keys="[CharacterInventoryItem.character_id]",
        backref="character",
        order_by="CharacterInventoryItem.id"
    )
    equipped_armor: Mapped[Optional["CharacterInventoryItem"]] = relationship(foreign_keys=[armor_inv_item_id], lazy="joined")
    equipped_shield: Mapped[Optional["CharacterInventoryItem"]] = relationship(foreign_keys=[shield_inv_item_id], lazy="joined")
    equipped_weapon1: Mapped[Optional["CharacterInventoryItem"]] = relationship(foreign_keys=[weapon1_inv_item_id], lazy="joined")
    equipped_weapon2: Mapped[Optional["CharacterInventoryItem"]] = relationship(foreign_keys=[weapon2_inv_item_id], lazy="joined")

    # === Слоты Активных Способностей ===
    active_ability_slot_1_id: Mapped[Optional[int]] = mapped_column(ForeignKey('abilities.id', use_alter=True), nullable=True)
    active_ability_slot_2_id: Mapped[Optional[int]] = mapped_column(ForeignKey('abilities.id', use_alter=True), nullable=True)
    active_ability_slot_3_id: Mapped[Optional[int]] = mapped_column(ForeignKey('abilities.id', use_alter=True), nullable=True)
    active_ability_slot_4_id: Mapped[Optional[int]] = mapped_column(ForeignKey('abilities.id', use_alter=True), nullable=True)
    active_ability_slot_5_id: Mapped[Optional[int]] = mapped_column(ForeignKey('abilities.id', use_alter=True), nullable=True)

    active_ability_slot_1_cooldown: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    active_ability_slot_2_cooldown: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    active_ability_slot_3_cooldown: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    active_ability_slot_4_cooldown: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)
    active_ability_slot_5_cooldown: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)

    active_ability_1: Mapped[Optional["Ability"]] = relationship(foreign_keys=[active_ability_slot_1_id], lazy="joined", post_update=True)
    active_ability_2: Mapped[Optional["Ability"]] = relationship(foreign_keys=[active_ability_slot_2_id], lazy="joined", post_update=True)
    active_ability_3: Mapped[Optional["Ability"]] = relationship(foreign_keys=[active_ability_slot_3_id], lazy="joined", post_update=True)
    active_ability_4: Mapped[Optional["Ability"]] = relationship(foreign_keys=[active_ability_slot_4_id], lazy="joined", post_update=True)
    active_ability_5: Mapped[Optional["Ability"]] = relationship(foreign_keys=[active_ability_slot_5_id], lazy="joined", post_update=True)
    # === КОНЕЦ Слотов ===

    # === Отслеживание Действий за Ход ===
    has_used_main_action: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false', nullable=False)
    has_used_bonus_action: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false', nullable=False)
    has_used_reaction: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false', nullable=False)
    # === КОНЕЦ Действий ===

    # --- Способности и эффекты ---
    available_abilities: Mapped[List["Ability"]] = relationship(
        "Ability",
        secondary=character_abilities,
        back_populates="characters"
    )
    active_status_effects: Mapped[List["StatusEffect"]] = relationship(
        "StatusEffect",
        secondary=character_status_effects,
        back_populates="characters"
    )
    custom_items: Mapped[List["CharacterCustomItem"]] = relationship(
        "CharacterCustomItem",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CharacterCustomItem.name"
    )

    # --- Заметки ---
    appearance_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    character_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    motivation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    background_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Связь "многие-к-одному" с User (владелец)
    owner: Mapped["User"] = relationship("User", back_populates="characters")


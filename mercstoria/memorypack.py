"""Merc Storia story data decryption + MemoryPack parser.

The schema below is reverse-engineered from `dump.cs` (Il2CppDumper output of
GameAssembly.dll). Every nested type, every member count, and every field
order matches the IL2CPP `[MemoryPackable]` definitions verbatim — that's
how round-tripping a real bundle yields byte-identical plaintext.

MemoryPack wire format (per Cysharp/MemoryPack README spec):
- Object         : (byte member_count, [values...])    0xFF = null, 0..249 = real count
- Collection     : (int length, [values...])           -1 = null
- String UTF8    : (int ~utf8_byte_count, int utf16_len, utf8_bytes)   -1 = null, 0 = empty
- Tuple          : fixed-size, no header               (KeyValuePair, ValueTuple)
- Nullable<T>    : (byte hasValue, T value)            0 = null

Game-specific:
- Encryption  : AES-256-CBC-PKCS7,
                key = PBKDF2-HMAC-SHA256("2147483647", "-2147483648", 1024, 32),
                IV = first 16 bytes of ciphertext.
- TimeSpan    : MemoryPack default = native struct = i64 ticks (8 bytes).
- Vector2/3   : unmanaged struct, raw float32 fields back-to-back.
- enums       : raw int32 (CompilerGenerated underlying type).
"""
import sys, struct, os, json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import UnityPy

from . import config as cfg

AES_KEY = cfg.derive_aes_key()


def decrypt(data: bytes) -> bytes:
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt


# ============================================================================
#                              Reader
# ============================================================================

class Reader:
    """Walks a MemoryPack (UTF-8 mode) plaintext buffer, returning a Python
    dict that mirrors the StoryYamlData object graph 1:1.

    Each `_mc` field is the raw member-count byte the Reader saw, kept so
    `Writer` can reproduce it byte-for-byte. We DON'T re-derive it from the
    field-list length on write — different game versions can ship the same
    type with different declared member counts.
    """
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    # primitives
    def byte(self):
        v = self.data[self.pos]; self.pos += 1; return v
    def bool(self):
        return bool(self.byte())
    def i32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]; self.pos += 4; return v
    def i64(self):
        v = struct.unpack_from('<q', self.data, self.pos)[0]; self.pos += 8; return v
    def f32(self):
        v = struct.unpack_from('<f', self.data, self.pos)[0]; self.pos += 4; return v

    # composite primitives
    def vec2(self):
        x = self.f32(); y = self.f32(); return [x, y]
    def vec3(self):
        x = self.f32(); y = self.f32(); z = self.f32(); return [x, y, z]
    def timespan(self):
        return self.i64()  # ticks

    def nullable_f32(self):
        # Nullable<float> is an unmanaged struct: 1 byte hasValue + 3 padding
        # + 4 byte float = 8 bytes raw memory copy (MemoryPack zero-encoding).
        # Stash hasValue and the raw f32 bits separately so we can reproduce
        # exact bytes on write — even when hasValue=0 and the value field
        # holds whatever floated in from prior heap reuse.
        has = self.byte()
        self.pos += 3   # padding (always zero per .NET runtime initobj)
        bits = self.data[self.pos:self.pos + 4]
        self.pos += 4
        f = struct.unpack('<f', bits)[0]
        if has == 0:
            # Wrap as dict so writer can emit the exact bytes back.
            return {"_null": True, "_bits": bits.hex()}
        return f

    def string(self):
        raw = self.i32()
        if raw == -1: return None
        if raw == 0:  return ""
        bc = ~raw
        _cc = self.i32()
        s = self.data[self.pos:self.pos + bc].decode('utf-8', errors='replace')
        self.pos += bc
        return s

    def string_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.string() for _ in range(n)]

    # generic object header — the "fields" list is a sequence of (name, fn)
    # tuples. Reads at most `mc` of them; remaining fields are recorded under
    # `_skipped` (their names) so Writer can replay the truncation exactly.
    # MemoryPack default-mode is version-tolerant on the read side: writing
    # mc=0 for a "default" StorySceneCharacterYamlData is a real pattern in
    # this game's bundles.
    def obj(self, fields):
        mc = self.byte()
        if mc == 0xFF: return None
        out = {"_mc": mc}
        n = min(mc, len(fields))
        for i in range(n):
            name, fn = fields[i]
            out[name] = fn()
        if mc < len(fields):
            out["_skipped"] = [name for name, _ in fields[mc:]]
        return out

    # nested-type readers
    def asset_param(self):
        return self.obj([
            ("Id", self.string), ("AssetType", self.i32),
            ("AssetName", self.string), ("SpriteStudioName", self.string),
            ("AnimationName", self.string),
            ("Position", self.vec3), ("Scale", self.vec3),
            ("PlayTimes", self.i32), ("ForcePlay", self.bool),
            ("FrameReset", self.bool), ("Delay", self.timespan),
        ])

    def blur(self):
        return self.obj([
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Quality", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def bright(self):
        return self.obj([
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def sepia(self):
        return self.obj([
            ("Type", self.i32),
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Saturation", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def anim_bg(self):
        return self.obj([
            ("StartPositionX", self.nullable_f32), ("StartPositionY", self.nullable_f32),
            ("StartScale", self.nullable_f32),
            ("PositionX", self.nullable_f32), ("PositionY", self.nullable_f32),
            ("Scale", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def bg_effect_param(self):
        return self.obj([
            ("Z", self.nullable_f32), ("AutoSkip", self.bool),
            ("Blur", self.blur), ("Bright", self.bright),
            ("Sepia", self.sepia), ("Animation", self.anim_bg),
        ])

    def bg_music(self):
        return self.obj([
            ("Name", self.string), ("AssetType", self.i32),
            ("AssetId", self.string), ("Mute", self.timespan),
            ("FadeIn", self.timespan), ("FadeOut", self.timespan),
            ("ForceFade", self.bool),
        ])

    def sound_effect(self):
        return self.obj([
            ("Name", self.string), ("AssetType", self.i32),
            ("AssetId", self.string), ("Type", self.i32),
            ("PlayTimes", self.i32), ("Interval", self.timespan),
            ("Delay", self.timespan), ("FadeIn", self.timespan),
            ("FadeOut", self.timespan),
        ])

    def sound_effect_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.sound_effect() for _ in range(n)]

    def cursor_param(self):
        return self.obj([
            ("Type", self.i32), ("Time", self.timespan),
            ("Position", self.vec2), ("Direction", self.i32),
            ("TouchPosition", self.vec2), ("TouchScale", self.vec2),
            ("Image", self.bool),
        ])

    def effect_param(self):
        return self.obj([
            ("FadeOut", self.timespan), ("FadeIn", self.timespan),
            ("FadeWait", self.timespan), ("ColorCode", self.string),
            ("AlphaFadeIn", self.bool), ("Duration", self.timespan),
            ("Delay", self.timespan), ("MoveType", self.i32),
            ("ScrollStartDelay", self.timespan), ("ShouUI", self.bool),
            ("AutoSkip", self.bool), ("AssetName", self.string),
            ("MovieAssetName", self.string), ("CursorParameter", self.cursor_param),
        ])

    def char_appearance(self):
        return self.obj([
            ("Type", self.i32),
            ("StartPositionX", self.nullable_f32), ("StartPositionY", self.nullable_f32),
            ("StartPositionZ", self.nullable_f32),
            ("EndPositionX", self.nullable_f32), ("EndPositionY", self.nullable_f32),
            ("EndPositionZ", self.nullable_f32),
            ("Duration", self.timespan), ("Active", self.bool),
        ])

    def character(self):
        return self.obj([
            ("TextureId", self.i32), ("FaceTextureId", self.i32),
            ("Type", self.i32), ("Key", self.string),
            ("DisplayName", self.string), ("Expression", self.i32),
            ("Emotion", self.i32), ("Active", self.bool),
            ("Appearance", self.char_appearance),
            ("Offset", self.vec3), ("Scale", self.vec3),
        ])

    def text_anim(self):
        return self.obj([
            ("Type", self.i32), ("Size", self.f32),
            ("Interval", self.timespan), ("FadeInDuration", self.timespan),
            ("ForceWait", self.bool),
        ])

    def asset_param_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.asset_param() for _ in range(n)]

    def scene(self):
        return self.obj([
            ("SceneId", self.i32),
            ("Speakers", self.string_array), ("Text", self.string),
            ("MessageWindowType", self.i32), ("MessageTextSize", self.i32),
            ("TextAnimation", self.text_anim), ("ForceShowAllText", self.bool),
            ("Background", self.string), ("Timezone", self.i32),
            ("BackgroundEffectParameter", self.bg_effect_param),
            ("BackgroundMusic", self.bg_music),
            ("SoundEffects", self.sound_effect_array),
            ("Effect", self.i32), ("EffectParameter", self.effect_param),
            ("DisableWipe", self.bool),
            ("Left", self.character), ("Center", self.character), ("Right", self.character),
            ("AssetsKeys", self.string_array),
            ("AssetParameters", self.asset_param_array),
            ("WaitTarget", self.string),
        ])

    def story(self):
        mc = self.byte()
        if mc == 0xFF: return None
        story_id = self.i32()
        n = self.i32()  # Dictionary<int, StorySceneYamlData>: int length, then (int, value) pairs
        scenes = []
        for _ in range(n):
            key = self.i32()
            scenes.append((key, self.scene()))
        return {"_mc": mc, "Id": story_id, "Scenes": scenes}

    # === Master-data records (Chapter/Story/Unit) ===
    # Field order = MemoryPackConstructor parameter order (NOT field declaration
    # order — they differ in Story/Unit). All enums are int32; TimeSpan is i64;
    # Vector2/3 are unmanaged f32 tuples.

    def chapter_record(self):
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Type", self.i32), ("EventId", self.i32),
            ("EventName", self.string), ("EventCountry", self.i32),
            ("Order", self.i32),
            ("MainStoryFilter", self.i32), ("EventStoryFilter", self.i32),
        ])

    def story_record(self):
        return self.obj([
            ("ChapterId", self.i32), ("StoryId", self.i32),
            ("Title", self.string), ("EventName", self.string),
            ("SubTitle", self.string),
            ("Type", self.i32), ("UnitId", self.i32),
            ("Children", self.string_array), ("Order", self.i32),
        ])

    def i32_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.i32() for _ in range(n)]

    def aura_trace(self):
        return self.obj([
            ("Target", self.string),
            ("Offset", self.vec3), ("Scale", self.vec3),
        ])

    def unit_record(self):
        return self.obj([
            ("Id", self.i32),
            ("PrefixName", self.string), ("MainName", self.string),
            ("Description", self.string),
            ("Country", self.i32), ("Rarity", self.i32),
            ("ActualRarity", self.i32), ("Attribute", self.i32),
            ("Weapon", self.i32), ("Growth", self.i32),
            ("MaxHp", self.i32), ("MaxAttack", self.i32),
            ("Speed", self.f32), ("AttackInterval", self.timespan),
            ("ReachValue", self.f32), ("Toughness", self.f32),
            ("AttackCount", self.i32), ("MultiHitCount", self.i32),
            ("MultiHitInterval", self.timespan),
            ("AttackRange", self.f32), ("SpBonus", self.i32),
            ("Reach", self.i32),
            ("FireRate", self.f32), ("WaterRate", self.f32),
            ("WindRate", self.f32), ("LightRate", self.f32),
            ("DarkRate", self.f32),
            ("Profession", self.string), ("WeaponLabel", self.string),
            ("Gender", self.i32), ("Age", self.string),
            ("AgeOrder", self.i32),
            ("Favorite", self.string), ("Personality", self.string),
            ("SkillIds", self.i32_array), ("SkillNames", self.string_array),
            ("AttackSoundEffectType", self.i32),
            ("AttackSoundEffectId", self.string),
            ("AttackEffectAssetName", self.string),
            ("AttackEffectAnimationName", self.string),
            ("AttackEffectPosition", self.vec2),
            ("AttackEffectMulti", self.bool),
            ("TargetEffectAssetName", self.string),
            ("TargetEffectAnimationName", self.string),
            ("RandomTargetEffectAnimationNames", self.string_array),
            ("TargetEffectAnimationDelay", self.timespan),
            ("TargetEffectMulti", self.bool),
            ("TargetEffectGround", self.bool),
            ("TargetEffectShowHealCommonEffect", self.bool),
            ("TargetEffectRandomSeed", self.vec2),
            ("HitFrame", self.f32),
            ("EffectPosition", self.vec3),
            ("OffsetPosition", self.vec2),
            ("ActType", self.i32), ("StoryId", self.i32),
            ("AuraTrace", self.aura_trace),
            ("FormChangeData", self.unit_record),
            ("NameFilter", self.i32), ("GenderFilter", self.i32),
            ("RarityFilter", self.i32), ("AttributeFilter", self.i32),
            ("WeaponFilter", self.i32), ("ReachFilter", self.i32),
            ("CountryFilter", self.i32), ("Order", self.i32),
        ])

    # === Master-data wrappers (outer mc=1, single Records[] field) ===

    def _master(self, record_fn):
        mc = self.byte()
        n = self.i32()
        if n == -1:
            return {"_mc": mc, "Records": None}
        return {"_mc": mc, "Records": [record_fn() for _ in range(n)]}

    def chapter_master(self): return self._master(self.chapter_record)
    def story_master(self):   return self._master(self.story_record)
    def unit_master(self):    return self._master(self.unit_record)

    # === 12 misc master records (ctor order from dump.cs) ===

    def background_record(self):
        # ctor: id, code, type, name, description, country, order, backgroundFilter, countryFilter
        return self.obj([
            ("Id", self.i32), ("Code", self.string),
            ("Type", self.i32), ("Name", self.string),
            ("Description", self.string), ("Country", self.i32),
            ("Order", self.i32),
            ("BackgroundFilter", self.i32), ("CountryFilter", self.i32),
        ])

    def background_music_record(self):
        # ctor: id, code, name, description, country, order, countryFilter
        return self.obj([
            ("Id", self.i32), ("Code", self.string),
            ("Name", self.string), ("Description", self.string),
            ("Country", self.i32), ("Order", self.i32),
            ("CountryFilter", self.i32),
        ])

    # --- GuildMapCondition nested types ---

    def guild_map_object(self):
        # ctor: id, key, position(Vec3), isFlip, clickable
        return self.obj([
            ("Id", self.string), ("Key", self.string),
            ("Position", self.vec3),
            ("IsFlip", self.bool), ("Clickable", self.bool),
        ])

    def guild_map_object_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_object() for _ in range(n)]

    def guild_map_sprite_studio(self):
        # ctor: asset, ssName, animationName, scale, colliderSize, colliderCenter, type
        return self.obj([
            ("Asset", self.string), ("SsName", self.string),
            ("AnimationName", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_sprite_studio_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_sprite_studio() for _ in range(n)]

    def guild_map_texture(self):
        # ctor: asset, scale, colliderSize, colliderCenter, type
        return self.obj([
            ("Asset", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_texture_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_texture() for _ in range(n)]

    def guild_map_prefab(self):
        # same shape as texture (separate type for type safety)
        return self.obj([
            ("Asset", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_prefab_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_prefab() for _ in range(n)]

    def guild_map_ss_landmark(self):
        # ctor: type, assetName, prefabName, animationName, rootPosition, ssPosition,
        #       ssScale, colliderPosition, colliderSize, squarePosition, squareSize,
        #       label, labelPosition, labelSize, labelFlip
        return self.obj([
            ("Type", self.i32),
            ("AssetName", self.string), ("PrefabName", self.string),
            ("AnimationName", self.string),
            ("RootPosition", self.vec2), ("SsPosition", self.vec2),
            ("SsScale", self.vec2),
            ("ColliderPosition", self.vec2), ("ColliderSize", self.vec2),
            ("SquarePosition", self.vec2), ("SquareSize", self.vec2),
            ("Label", self.string),
            ("LabelPosition", self.vec2), ("LabelSize", self.vec2),
            ("LabelFlip", self.bool),
        ])

    def guild_map_ss_landmark_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_ss_landmark() for _ in range(n)]

    def guild_map_tex_landmark(self):
        # ctor: type, asset, rootPosition, size, colliderPosition, colliderSize, squarePosition, squareSize
        return self.obj([
            ("Type", self.string), ("Asset", self.string),
            ("RootPosition", self.vec2), ("Size", self.vec2),
            ("ColliderPosition", self.vec2), ("ColliderSize", self.vec2),
            ("SquarePosition", self.vec2), ("SquareSize", self.vec2),
        ])

    def guild_map_tex_landmark_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.guild_map_tex_landmark() for _ in range(n)]

    def guild_map_constant(self):
        # ctor: bgm, background1, background2, backgroundMountain1, backgroundMountain2,
        #       backgroundSky1, backgroundSky2, unmovableSquareIds, spriteStudioObjects,
        #       textureObjects, prefabObjects, spriteStudioLandmarks, textureLandmarks,
        #       hideBackgroundCloud
        return self.obj([
            ("Bgm", self.string),
            ("Background1", self.string), ("Background2", self.string),
            ("BackgroundMountain1", self.string), ("BackgroundMountain2", self.string),
            ("BackgroundSky1", self.string), ("BackgroundSky2", self.string),
            ("UnmovableSquareIds", self.string_array),
            ("SpriteStudioObjects", self.guild_map_sprite_studio_array),
            ("TextureObjects", self.guild_map_texture_array),
            ("PrefabObjects", self.guild_map_prefab_array),
            ("SpriteStudioLandmarks", self.guild_map_ss_landmark_array),
            ("TextureLandmarks", self.guild_map_tex_landmark_array),
            ("HideBackgroundCloud", self.bool),
        ])

    def guild_map_condition_record(self):
        # ctor: id, name, objects, constantData
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Objects", self.guild_map_object_array),
            ("ConstantData", self.guild_map_constant),
        ])

    def guild_tournament_record(self):
        # ctor: id, identifier(enum), block(enum), rank, guildName
        return self.obj([
            ("Id", self.i32),
            ("Identifier", self.i32), ("Block", self.i32),
            ("Rank", self.i32), ("GuildName", self.string),
        ])

    def leader_style_record(self):
        # ctor: id, name, description, unitId, order
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("UnitId", self.i32), ("Order", self.i32),
        ])

    def loading_comic_record(self):
        # ctor: id, name
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
        ])

    def main_character_style_record(self):
        # ctor: id, name, description, unitId, order — same shape as leader_style
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("UnitId", self.i32), ("Order", self.i32),
        ])

    def memorial_quest_record(self):
        # ctor: id, name, description, bgmId
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string), ("BgmId", self.string),
        ])

    def monster_record(self):
        # ctor: id, name, description, rarity, attribute, skillType, scale, baseScale,
        #       hardness, damagePartsCount, cost, callInterval, attackCount, multiHitCount,
        #       multiHitInterval, attackRange, maxHp, maxAttack, seedAttack, speed,
        #       attackInterval, reachValue, toughness, fireRate, waterRate, windRate,
        #       lightRate, darkRate, appearStageNames, attackSoundEffectType,
        #       attackSoundEffectId, attackEffectAssetName, attackEffectAnimationName,
        #       attackEffectPosition, attackEffectMulti, targetEffectAssetName,
        #       targetEffectAnimationName, targetEffectAnimationDelay, targetEffectMulti,
        #       targetEffectGround, hitFrame, effectPosition, offsetPosition,
        #       nameFilter, rarityFilter, attributeFilter, hardnessFilter, reachFilter,
        #       skillFilter, order
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("Rarity", self.i32), ("Attribute", self.i32),
            ("SkillType", self.i32),
            ("Scale", self.f32), ("BaseScale", self.f32),
            ("Hardness", self.i32), ("DamagePartsCount", self.i32),
            ("Cost", self.i32), ("CallInterval", self.timespan),
            ("AttackCount", self.i32), ("MultiHitCount", self.i32),
            ("MultiHitInterval", self.timespan),
            ("AttackRange", self.f32),
            ("MaxHp", self.i32), ("MaxAttack", self.i32),
            ("SeedAttack", self.i32),
            ("Speed", self.f32), ("AttackInterval", self.timespan),
            ("ReachValue", self.f32), ("Toughness", self.f32),
            ("FireRate", self.f32), ("WaterRate", self.f32),
            ("WindRate", self.f32), ("LightRate", self.f32),
            ("DarkRate", self.f32),
            ("AppearStageNames", self.string),
            ("AttackSoundEffectType", self.i32),
            ("AttackSoundEffectId", self.string),
            ("AttackEffectAssetName", self.string),
            ("AttackEffectAnimationName", self.string),
            ("AttackEffectPosition", self.vec2),
            ("AttackEffectMulti", self.bool),
            ("TargetEffectAssetName", self.string),
            ("TargetEffectAnimationName", self.string),
            ("TargetEffectAnimationDelay", self.timespan),
            ("TargetEffectMulti", self.bool),
            ("TargetEffectGround", self.bool),
            ("HitFrame", self.f32),
            ("EffectPosition", self.vec3),
            ("OffsetPosition", self.vec2),
            ("NameFilter", self.i32), ("RarityFilter", self.i32),
            ("AttributeFilter", self.i32),
            ("HardnessFilter", self.i32), ("ReachFilter", self.i32),
            ("SkillFilter", self.i32),
            ("Order", self.i32),
        ])

    def square_background_record(self):
        # ctor: id, name, countryFilter
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("CountryFilter", self.i32),
        ])

    def stamp_record(self):
        # ctor: id, name, displayName, index, type, iconAssetName
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("DisplayName", self.string),
            ("Index", self.i32), ("Type", self.i32),
            ("IconAssetName", self.string),
        ])

    def f32_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.f32() for _ in range(n)]

    def f32_array_array(self):
        n = self.i32()
        if n == -1: return None
        return [self.f32_array() for _ in range(n)]

    def unit_skill_effect_record(self):
        # ctor: id, name, description, category, type, targets(int[]), parameters(float[][])
        return self.obj([
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("Category", self.i32), ("Type", self.i32),
            ("Targets", self.i32_array),
            ("Parameters", self.f32_array_array),
        ])

    # === 12 misc master wrappers ===

    def background_master(self):       return self._master(self.background_record)
    def background_music_master(self): return self._master(self.background_music_record)
    def guild_map_condition_master(self): return self._master(self.guild_map_condition_record)
    def guild_tournament_master(self):    return self._master(self.guild_tournament_record)
    def leader_style_master(self):        return self._master(self.leader_style_record)
    def loading_comic_master(self):       return self._master(self.loading_comic_record)
    def main_character_style_master(self):return self._master(self.main_character_style_record)
    def memorial_quest_master(self):      return self._master(self.memorial_quest_record)
    def monster_master(self):             return self._master(self.monster_record)
    def square_background_master(self):   return self._master(self.square_background_record)
    def stamp_master(self):               return self._master(self.stamp_record)
    def unit_skill_effect_master(self):   return self._master(self.unit_skill_effect_record)


# ============================================================================
#                              Writer
# ============================================================================

class Writer:
    """Symmetric to Reader. Fed a dict produced by Reader.story(), emits the
    exact same bytes. Tested by reading every cached story bundle and asserting
    that `serialize_story(read_story_bundle(...)) == decrypted_plaintext`."""
    def __init__(self):
        self.buf = bytearray()
    def bytes_(self): return bytes(self.buf)

    def byte(self, v): self.buf.append(v)
    def bool(self, v): self.byte(1 if v else 0)
    def i32(self, v):  self.buf.extend(struct.pack('<i', v))
    def i64(self, v):  self.buf.extend(struct.pack('<q', v))
    def f32(self, v):  self.buf.extend(struct.pack('<f', v))

    def vec2(self, v): self.f32(v[0]); self.f32(v[1])
    def vec3(self, v): self.f32(v[0]); self.f32(v[1]); self.f32(v[2])
    def timespan(self, v): self.i64(v)

    def nullable_f32(self, v):
        # Mirror of Reader.nullable_f32: 1 byte hasValue + 3 padding + 4 byte
        # float. Reader returns float for hasValue=1, dict {_null,_bits} for
        # hasValue=0 with the original bits preserved.
        if isinstance(v, dict) and v.get("_null"):
            self.byte(0)
            self.buf.extend(b'\x00\x00\x00')
            self.buf.extend(bytes.fromhex(v["_bits"]))
            return
        if v is None:
            self.byte(0)
            self.buf.extend(b'\x00\x00\x00')
            self.buf.extend(b'\x00\x00\x00\x00')
            return
        self.byte(1)
        self.buf.extend(b'\x00\x00\x00')
        self.f32(v)

    def string(self, s):
        if s is None: self.i32(-1); return
        if s == "":   self.i32(0);  return
        enc = s.encode('utf-8')
        self.i32(~len(enc))
        self.i32(len(s))
        self.buf.extend(enc)

    def string_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for s in arr: self.string(s)

    def header(self, obj):
        if obj is None: self.byte(0xFF); return False
        self.byte(obj["_mc"]); return True

    def write_obj(self, obj, fields):
        """Write `mc` field followed by min(mc, len(fields)) entries.
        Mirror of Reader.obj() — used for every nested struct."""
        if obj is None: self.byte(0xFF); return
        mc = obj["_mc"]
        self.byte(mc)
        n = min(mc, len(fields))
        for i in range(n):
            name, fn = fields[i]
            fn(obj[name])

    def asset_param(self, o):
        self.write_obj(o, [
            ("Id", self.string), ("AssetType", self.i32),
            ("AssetName", self.string), ("SpriteStudioName", self.string),
            ("AnimationName", self.string),
            ("Position", self.vec3), ("Scale", self.vec3),
            ("PlayTimes", self.i32), ("ForcePlay", self.bool),
            ("FrameReset", self.bool), ("Delay", self.timespan),
        ])

    def blur(self, o):
        self.write_obj(o, [
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Quality", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def bright(self, o):
        self.write_obj(o, [
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def sepia(self, o):
        self.write_obj(o, [
            ("Type", self.i32),
            ("StartPower", self.nullable_f32), ("Power", self.nullable_f32),
            ("Saturation", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def anim_bg(self, o):
        self.write_obj(o, [
            ("StartPositionX", self.nullable_f32), ("StartPositionY", self.nullable_f32),
            ("StartScale", self.nullable_f32),
            ("PositionX", self.nullable_f32), ("PositionY", self.nullable_f32),
            ("Scale", self.nullable_f32),
            ("Duration", self.timespan), ("Delay", self.timespan),
        ])

    def bg_effect_param(self, o):
        self.write_obj(o, [
            ("Z", self.nullable_f32), ("AutoSkip", self.bool),
            ("Blur", self.blur), ("Bright", self.bright),
            ("Sepia", self.sepia), ("Animation", self.anim_bg),
        ])

    def bg_music(self, o):
        self.write_obj(o, [
            ("Name", self.string), ("AssetType", self.i32),
            ("AssetId", self.string), ("Mute", self.timespan),
            ("FadeIn", self.timespan), ("FadeOut", self.timespan),
            ("ForceFade", self.bool),
        ])

    def sound_effect(self, o):
        self.write_obj(o, [
            ("Name", self.string), ("AssetType", self.i32),
            ("AssetId", self.string), ("Type", self.i32),
            ("PlayTimes", self.i32), ("Interval", self.timespan),
            ("Delay", self.timespan), ("FadeIn", self.timespan),
            ("FadeOut", self.timespan),
        ])

    def sound_effect_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.sound_effect(x)

    def cursor_param(self, o):
        self.write_obj(o, [
            ("Type", self.i32), ("Time", self.timespan),
            ("Position", self.vec2), ("Direction", self.i32),
            ("TouchPosition", self.vec2), ("TouchScale", self.vec2),
            ("Image", self.bool),
        ])

    def effect_param(self, o):
        self.write_obj(o, [
            ("FadeOut", self.timespan), ("FadeIn", self.timespan),
            ("FadeWait", self.timespan), ("ColorCode", self.string),
            ("AlphaFadeIn", self.bool), ("Duration", self.timespan),
            ("Delay", self.timespan), ("MoveType", self.i32),
            ("ScrollStartDelay", self.timespan), ("ShouUI", self.bool),
            ("AutoSkip", self.bool), ("AssetName", self.string),
            ("MovieAssetName", self.string), ("CursorParameter", self.cursor_param),
        ])

    def char_appearance(self, o):
        self.write_obj(o, [
            ("Type", self.i32),
            ("StartPositionX", self.nullable_f32), ("StartPositionY", self.nullable_f32),
            ("StartPositionZ", self.nullable_f32),
            ("EndPositionX", self.nullable_f32), ("EndPositionY", self.nullable_f32),
            ("EndPositionZ", self.nullable_f32),
            ("Duration", self.timespan), ("Active", self.bool),
        ])

    def character(self, o):
        self.write_obj(o, [
            ("TextureId", self.i32), ("FaceTextureId", self.i32),
            ("Type", self.i32), ("Key", self.string),
            ("DisplayName", self.string), ("Expression", self.i32),
            ("Emotion", self.i32), ("Active", self.bool),
            ("Appearance", self.char_appearance),
            ("Offset", self.vec3), ("Scale", self.vec3),
        ])

    def text_anim(self, o):
        self.write_obj(o, [
            ("Type", self.i32), ("Size", self.f32),
            ("Interval", self.timespan), ("FadeInDuration", self.timespan),
            ("ForceWait", self.bool),
        ])

    def asset_param_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.asset_param(x)

    def scene(self, o):
        self.write_obj(o, [
            ("SceneId", self.i32),
            ("Speakers", self.string_array), ("Text", self.string),
            ("MessageWindowType", self.i32), ("MessageTextSize", self.i32),
            ("TextAnimation", self.text_anim), ("ForceShowAllText", self.bool),
            ("Background", self.string), ("Timezone", self.i32),
            ("BackgroundEffectParameter", self.bg_effect_param),
            ("BackgroundMusic", self.bg_music),
            ("SoundEffects", self.sound_effect_array),
            ("Effect", self.i32), ("EffectParameter", self.effect_param),
            ("DisableWipe", self.bool),
            ("Left", self.character), ("Center", self.character), ("Right", self.character),
            ("AssetsKeys", self.string_array),
            ("AssetParameters", self.asset_param_array),
            ("WaitTarget", self.string),
        ])

    def story(self, o):
        if o is None: self.byte(0xFF); return
        self.byte(o["_mc"])
        self.i32(o["Id"])
        self.i32(len(o["Scenes"]))
        for key, sc in o["Scenes"]:
            self.i32(key)
            self.scene(sc)

    # === Master-data records (Chapter/Story/Unit) ===
    # Field order = MemoryPackConstructor parameter order (NOT field declaration
    # order — they differ in Story/Unit). All enums are int32; TimeSpan is i64;
    # Vector2/3 are unmanaged f32 tuples.

    def chapter_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Type", self.i32), ("EventId", self.i32),
            ("EventName", self.string), ("EventCountry", self.i32),
            ("Order", self.i32),
            ("MainStoryFilter", self.i32), ("EventStoryFilter", self.i32),
        ])

    def story_record(self, o):
        # ctor: chapterId, storyId, title, eventName, subTitle, type, unitId,
        #       children, order — note title/eventName/subTitle order in the
        #       ctor differs from the field declaration.
        self.write_obj(o, [
            ("ChapterId", self.i32), ("StoryId", self.i32),
            ("Title", self.string), ("EventName", self.string),
            ("SubTitle", self.string),
            ("Type", self.i32), ("UnitId", self.i32),
            ("Children", self.string_array), ("Order", self.i32),
        ])

    def unit_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32),
            ("PrefixName", self.string), ("MainName", self.string),
            ("Description", self.string),
            ("Country", self.i32), ("Rarity", self.i32),
            ("ActualRarity", self.i32), ("Attribute", self.i32),
            ("Weapon", self.i32), ("Growth", self.i32),
            ("MaxHp", self.i32), ("MaxAttack", self.i32),
            ("Speed", self.f32), ("AttackInterval", self.timespan),
            ("ReachValue", self.f32), ("Toughness", self.f32),
            ("AttackCount", self.i32), ("MultiHitCount", self.i32),
            ("MultiHitInterval", self.timespan),
            ("AttackRange", self.f32), ("SpBonus", self.i32),
            ("Reach", self.i32),
            ("FireRate", self.f32), ("WaterRate", self.f32),
            ("WindRate", self.f32), ("LightRate", self.f32),
            ("DarkRate", self.f32),
            ("Profession", self.string), ("WeaponLabel", self.string),
            ("Gender", self.i32), ("Age", self.string),
            ("AgeOrder", self.i32),
            ("Favorite", self.string), ("Personality", self.string),
            ("SkillIds", self.i32_array), ("SkillNames", self.string_array),
            ("AttackSoundEffectType", self.i32),
            ("AttackSoundEffectId", self.string),
            ("AttackEffectAssetName", self.string),
            ("AttackEffectAnimationName", self.string),
            ("AttackEffectPosition", self.vec2),
            ("AttackEffectMulti", self.bool),
            ("TargetEffectAssetName", self.string),
            ("TargetEffectAnimationName", self.string),
            ("RandomTargetEffectAnimationNames", self.string_array),
            ("TargetEffectAnimationDelay", self.timespan),
            ("TargetEffectMulti", self.bool),
            ("TargetEffectGround", self.bool),
            ("TargetEffectShowHealCommonEffect", self.bool),
            ("TargetEffectRandomSeed", self.vec2),
            ("HitFrame", self.f32),
            ("EffectPosition", self.vec3),
            ("OffsetPosition", self.vec2),
            ("ActType", self.i32), ("StoryId", self.i32),
            ("AuraTrace", self.aura_trace),
            ("FormChangeData", self.unit_record),
            ("NameFilter", self.i32), ("GenderFilter", self.i32),
            ("RarityFilter", self.i32), ("AttributeFilter", self.i32),
            ("WeaponFilter", self.i32), ("ReachFilter", self.i32),
            ("CountryFilter", self.i32), ("Order", self.i32),
        ])

    def aura_trace(self, o):
        self.write_obj(o, [
            ("Target", self.string),
            ("Offset", self.vec3), ("Scale", self.vec3),
        ])

    def i32_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for v in arr: self.i32(v)

    def chapter_master(self, o):
        self.byte(o["_mc"])
        recs = o["Records"]
        if recs is None: self.i32(-1); return
        self.i32(len(recs))
        for r in recs: self.chapter_record(r)

    def story_master(self, o):
        self.byte(o["_mc"])
        recs = o["Records"]
        if recs is None: self.i32(-1); return
        self.i32(len(recs))
        for r in recs: self.story_record(r)

    def unit_master(self, o):
        self.byte(o["_mc"])
        recs = o["Records"]
        if recs is None: self.i32(-1); return
        self.i32(len(recs))
        for r in recs: self.unit_record(r)

    # === 12 misc master records ===

    def background_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Code", self.string),
            ("Type", self.i32), ("Name", self.string),
            ("Description", self.string), ("Country", self.i32),
            ("Order", self.i32),
            ("BackgroundFilter", self.i32), ("CountryFilter", self.i32),
        ])

    def background_music_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Code", self.string),
            ("Name", self.string), ("Description", self.string),
            ("Country", self.i32), ("Order", self.i32),
            ("CountryFilter", self.i32),
        ])

    def guild_map_object(self, o):
        self.write_obj(o, [
            ("Id", self.string), ("Key", self.string),
            ("Position", self.vec3),
            ("IsFlip", self.bool), ("Clickable", self.bool),
        ])

    def guild_map_object_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_object(x)

    def guild_map_sprite_studio(self, o):
        self.write_obj(o, [
            ("Asset", self.string), ("SsName", self.string),
            ("AnimationName", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_sprite_studio_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_sprite_studio(x)

    def guild_map_texture(self, o):
        self.write_obj(o, [
            ("Asset", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_texture_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_texture(x)

    def guild_map_prefab(self, o):
        self.write_obj(o, [
            ("Asset", self.string),
            ("Scale", self.vec2), ("ColliderSize", self.vec2),
            ("ColliderCenter", self.vec2),
            ("Type", self.string),
        ])

    def guild_map_prefab_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_prefab(x)

    def guild_map_ss_landmark(self, o):
        self.write_obj(o, [
            ("Type", self.i32),
            ("AssetName", self.string), ("PrefabName", self.string),
            ("AnimationName", self.string),
            ("RootPosition", self.vec2), ("SsPosition", self.vec2),
            ("SsScale", self.vec2),
            ("ColliderPosition", self.vec2), ("ColliderSize", self.vec2),
            ("SquarePosition", self.vec2), ("SquareSize", self.vec2),
            ("Label", self.string),
            ("LabelPosition", self.vec2), ("LabelSize", self.vec2),
            ("LabelFlip", self.bool),
        ])

    def guild_map_ss_landmark_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_ss_landmark(x)

    def guild_map_tex_landmark(self, o):
        self.write_obj(o, [
            ("Type", self.string), ("Asset", self.string),
            ("RootPosition", self.vec2), ("Size", self.vec2),
            ("ColliderPosition", self.vec2), ("ColliderSize", self.vec2),
            ("SquarePosition", self.vec2), ("SquareSize", self.vec2),
        ])

    def guild_map_tex_landmark_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for x in arr: self.guild_map_tex_landmark(x)

    def guild_map_constant(self, o):
        self.write_obj(o, [
            ("Bgm", self.string),
            ("Background1", self.string), ("Background2", self.string),
            ("BackgroundMountain1", self.string), ("BackgroundMountain2", self.string),
            ("BackgroundSky1", self.string), ("BackgroundSky2", self.string),
            ("UnmovableSquareIds", self.string_array),
            ("SpriteStudioObjects", self.guild_map_sprite_studio_array),
            ("TextureObjects", self.guild_map_texture_array),
            ("PrefabObjects", self.guild_map_prefab_array),
            ("SpriteStudioLandmarks", self.guild_map_ss_landmark_array),
            ("TextureLandmarks", self.guild_map_tex_landmark_array),
            ("HideBackgroundCloud", self.bool),
        ])

    def guild_map_condition_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Objects", self.guild_map_object_array),
            ("ConstantData", self.guild_map_constant),
        ])

    def guild_tournament_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32),
            ("Identifier", self.i32), ("Block", self.i32),
            ("Rank", self.i32), ("GuildName", self.string),
        ])

    def leader_style_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("UnitId", self.i32), ("Order", self.i32),
        ])

    def loading_comic_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
        ])

    def main_character_style_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("UnitId", self.i32), ("Order", self.i32),
        ])

    def memorial_quest_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string), ("BgmId", self.string),
        ])

    def monster_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("Rarity", self.i32), ("Attribute", self.i32),
            ("SkillType", self.i32),
            ("Scale", self.f32), ("BaseScale", self.f32),
            ("Hardness", self.i32), ("DamagePartsCount", self.i32),
            ("Cost", self.i32), ("CallInterval", self.timespan),
            ("AttackCount", self.i32), ("MultiHitCount", self.i32),
            ("MultiHitInterval", self.timespan),
            ("AttackRange", self.f32),
            ("MaxHp", self.i32), ("MaxAttack", self.i32),
            ("SeedAttack", self.i32),
            ("Speed", self.f32), ("AttackInterval", self.timespan),
            ("ReachValue", self.f32), ("Toughness", self.f32),
            ("FireRate", self.f32), ("WaterRate", self.f32),
            ("WindRate", self.f32), ("LightRate", self.f32),
            ("DarkRate", self.f32),
            ("AppearStageNames", self.string),
            ("AttackSoundEffectType", self.i32),
            ("AttackSoundEffectId", self.string),
            ("AttackEffectAssetName", self.string),
            ("AttackEffectAnimationName", self.string),
            ("AttackEffectPosition", self.vec2),
            ("AttackEffectMulti", self.bool),
            ("TargetEffectAssetName", self.string),
            ("TargetEffectAnimationName", self.string),
            ("TargetEffectAnimationDelay", self.timespan),
            ("TargetEffectMulti", self.bool),
            ("TargetEffectGround", self.bool),
            ("HitFrame", self.f32),
            ("EffectPosition", self.vec3),
            ("OffsetPosition", self.vec2),
            ("NameFilter", self.i32), ("RarityFilter", self.i32),
            ("AttributeFilter", self.i32),
            ("HardnessFilter", self.i32), ("ReachFilter", self.i32),
            ("SkillFilter", self.i32),
            ("Order", self.i32),
        ])

    def square_background_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("CountryFilter", self.i32),
        ])

    def stamp_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("DisplayName", self.string),
            ("Index", self.i32), ("Type", self.i32),
            ("IconAssetName", self.string),
        ])

    def f32_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for v in arr: self.f32(v)

    def f32_array_array(self, arr):
        if arr is None: self.i32(-1); return
        self.i32(len(arr))
        for v in arr: self.f32_array(v)

    def unit_skill_effect_record(self, o):
        self.write_obj(o, [
            ("Id", self.i32), ("Name", self.string),
            ("Description", self.string),
            ("Category", self.i32), ("Type", self.i32),
            ("Targets", self.i32_array),
            ("Parameters", self.f32_array_array),
        ])

    def _write_master(self, o, record_fn):
        self.byte(o["_mc"])
        recs = o["Records"]
        if recs is None: self.i32(-1); return
        self.i32(len(recs))
        for r in recs: record_fn(r)

    def background_master(self, o):       self._write_master(o, self.background_record)
    def background_music_master(self, o): self._write_master(o, self.background_music_record)
    def guild_map_condition_master(self, o): self._write_master(o, self.guild_map_condition_record)
    def guild_tournament_master(self, o):    self._write_master(o, self.guild_tournament_record)
    def leader_style_master(self, o):        self._write_master(o, self.leader_style_record)
    def loading_comic_master(self, o):       self._write_master(o, self.loading_comic_record)
    def main_character_style_master(self, o):self._write_master(o, self.main_character_style_record)
    def memorial_quest_master(self, o):      self._write_master(o, self.memorial_quest_record)
    def monster_master(self, o):             self._write_master(o, self.monster_record)
    def square_background_master(self, o):   self._write_master(o, self.square_background_record)
    def stamp_master(self, o):               self._write_master(o, self.stamp_record)
    def unit_skill_effect_master(self, o):   self._write_master(o, self.unit_skill_effect_record)


def serialize_story(story: dict) -> bytes:
    w = Writer(); w.story(story); return w.bytes_()


def serialize_chapter_master(o: dict) -> bytes:
    w = Writer(); w.chapter_master(o); return w.bytes_()


def serialize_story_master(o: dict) -> bytes:
    w = Writer(); w.story_master(o); return w.bytes_()


def serialize_unit_master(o: dict) -> bytes:
    w = Writer(); w.unit_master(o); return w.bytes_()


def serialize_background_master(o: dict) -> bytes:
    w = Writer(); w.background_master(o); return w.bytes_()


def serialize_background_music_master(o: dict) -> bytes:
    w = Writer(); w.background_music_master(o); return w.bytes_()


def serialize_guild_map_condition_master(o: dict) -> bytes:
    w = Writer(); w.guild_map_condition_master(o); return w.bytes_()


def serialize_guild_tournament_master(o: dict) -> bytes:
    w = Writer(); w.guild_tournament_master(o); return w.bytes_()


def serialize_leader_style_master(o: dict) -> bytes:
    w = Writer(); w.leader_style_master(o); return w.bytes_()


def serialize_loading_comic_master(o: dict) -> bytes:
    w = Writer(); w.loading_comic_master(o); return w.bytes_()


def serialize_main_character_style_master(o: dict) -> bytes:
    w = Writer(); w.main_character_style_master(o); return w.bytes_()


def serialize_memorial_quest_master(o: dict) -> bytes:
    w = Writer(); w.memorial_quest_master(o); return w.bytes_()


def serialize_monster_master(o: dict) -> bytes:
    w = Writer(); w.monster_master(o); return w.bytes_()


def serialize_square_background_master(o: dict) -> bytes:
    w = Writer(); w.square_background_master(o); return w.bytes_()


def serialize_stamp_master(o: dict) -> bytes:
    w = Writer(); w.stamp_master(o); return w.bytes_()


def serialize_unit_skill_effect_master(o: dict) -> bytes:
    w = Writer(); w.unit_skill_effect_master(o); return w.bytes_()


def extract_bundle_textasset(bundle_path: str) -> tuple:
    """Extract (asset_name, encrypted_payload_bytes) from a bundle's TextAsset.

    Returns (None, None) when the bundle has no TextAsset (e.g. font/scene
    bundles). Pure helper — no decryption, no MemoryPack parsing.
    """
    env = UnityPy.load(bundle_path)
    for obj in env.objects:
        if obj.type.name == "TextAsset":
            obj.reset()
            reader = obj.reader
            reader.Position = obj.byte_start
            raw = reader.read(obj.byte_size)
            pos = 0
            name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            name = raw[pos:pos+name_len].decode('utf-8', errors='replace'); pos += name_len
            pos = (pos + 3) & ~3
            script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
            script_data = raw[pos:pos+script_len]
            return name, script_data
    return None, None


def process_story_bundle(bundle_path: str):
    """Decrypt + parse a story bundle. Returns (story_dict, plaintext_bytes)
    or (None, None) on a TextAsset-less bundle. Used by `check_roundtrip`."""
    name, encrypted = extract_bundle_textasset(bundle_path)
    if encrypted is None:
        return None, None
    plaintext = decrypt(encrypted)
    return Reader(plaintext).story(), plaintext

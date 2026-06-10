"""Fix speaker names by replacing ALL MemoryPack string occurrences globally.

The previous approach only found strings in the Speakers[] array pattern.
Speaker names also appear in StorySceneCharacterYamlData.Key and .DisplayName
fields. The simplest fix: find every MemoryPack-encoded string matching a
translation key and replace it everywhere.
"""
import sys, io, struct, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import hashes, padding as sym_padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import UnityPy

kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                  salt=b"-2147483648", iterations=1024)
AES_KEY = kdf.derive(b"2147483647")

def decrypt(data):
    iv, ct = data[:16], data[16:]
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    dec = cipher.decryptor()
    pt = dec.update(ct) + dec.finalize()
    pad = pt[-1]
    if 1 <= pad <= 16 and all(b == pad for b in pt[-pad:]):
        return pt[:-pad]
    return pt

def encrypt(plaintext):
    iv = os.urandom(16)
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return iv + ct

def encode_mempack_string(s):
    """Encode a string in MemoryPack format: int32(~byte_len) + int32(char_count) + utf8_bytes"""
    if s is None: return struct.pack('<i', -1)
    if s == "": return struct.pack('<i', 0)
    encoded = s.encode('utf-8')
    return struct.pack('<i', ~len(encoded)) + struct.pack('<i', len(s)) + encoded

def replace_all_mempack_strings(data, translations):
    """Replace every MemoryPack string occurrence of translation keys with their values."""
    result = bytearray(data)

    # Build a map of original encoded bytes -> replacement encoded bytes
    replacements = []
    for orig, trans in translations.items():
        if orig == trans:
            continue
        orig_encoded = encode_mempack_string(orig)
        trans_encoded = encode_mempack_string(trans)
        replacements.append((orig_encoded, trans_encoded, orig, trans))

    # Sort by longest first to avoid partial matches
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    # For each replacement, find all occurrences and replace
    # We need to handle the fact that replacements can change offsets
    # So we work from end to start
    for orig_bytes, trans_bytes, orig_str, trans_str in replacements:
        positions = []
        idx = 0
        while True:
            idx = bytes(result).find(orig_bytes, idx)
            if idx < 0:
                break
            positions.append(idx)
            idx += len(orig_bytes)

        if not positions:
            continue

        # Replace from end to start to preserve earlier offsets
        for pos in reversed(positions):
            result[pos:pos + len(orig_bytes)] = trans_bytes

        print(f"  '{orig_str}' -> '{trans_str}': {len(positions)} replacements")

    return bytes(result)


# Speaker name translations
SPEAKER_TRANSLATIONS = {
    "たいてんき": "大天鬼",
    "しずめき": "镇鬼",
    "メルク": "梅露可",
    "たづ": "多鹤",
    "ちひろ": "千寻",
    "ちとせ": "千岁",
    "蕎麦屋のおかみ": "荞麦店老板娘",
    "櫛売り": "梳子商贩",
    "変装こんこ": "变装小狐",
    "イブギオウ": "伊布基王",
}

# Dialogue translations (same as translate_1621.py)
DIALOGUE_TRANSLATIONS = {
    "「……。」": "「……。」",
    "「たいてんき、２品目で力尽きる……、と。」": "「大天鬼，在第二道菜就力尽了……记下来。」",
    "「はやーっ！？」": "「太快了吧！？」",
    "「勝負にもならなかったのですよ。」": "「根本就不算比赛呢。」",
    "「おかみさん。\r\n食後の甘味に、蕎麦羊羹をお願いいたします。」": "「老板娘。\r\n饭后甜点，请来一份荞麦羊羹。」",
    "「す、すごい！\r\nあれだけ食べておいてまだ甘味まで食べる気だ……！」": "「好、好厉害！\r\n吃了那么多居然还要吃甜点……！」",
    "「蕎麦羊羹ですか、いいですね。\r\n私にもお願いいたします。」": "「荞麦羊羹吗，不错呢。\r\n也请给我来一份。」",
    "「お兄さんはあの子の仲間ではなかったのですよ！？\r\nさっきまで持ち上げてた相手を尻目に、\r\nなに普通に羊羹を食べようとしてるのですよ！」": "「你不是那孩子的同伴吗！？\r\n刚才还在吹捧的对象被晾在一边，\r\n怎么就若无其事地吃起羊羹了呢！」",
    "「おやおや、あの方の偉大さがわかっていないようですね。\r\nたいてんき様は百鬼夜行の頭領となられる大妖怪。\r\nこのしずめきごときの力など、本来必要ないのです。」": "「哎呀哎呀，你们似乎不了解那位大人的伟大啊。\r\n大天鬼大人可是要成为百鬼夜行统领的大妖怪。\r\n像镇鬼这种程度的力量，本来就不需要的。」",
    "「ねーっ、たいてんき様っ！」": "「对吧——大天鬼大人！」",
    "「大妖怪様、燃え尽きてますけど！？」": "「大妖怪大人，已经燃尽了啊！？」",
    "「これが蕎麦羊羹……、\r\nはじめて食べましたがなかなかの美味ですね。」": "「这就是荞麦羊羹……\r\n第一次吃，味道相当不错呢。」",
    "「聞いてねえ！」": "「根本没在听啊！」",
    "「ふ……、ふふふ……。」": "「呼……呵呵呵……」",
    "「あっ、よみがえった。」": "「啊，复活了。」",
    "「かーかっかっか！\r\n刺し違えてでもわしに屈さぬとするその覚悟、\r\n敵ながらあっぱれ、褒めて遣わす！」": "「哈——哈哈哈！\r\n即使同归于尽也不向吾屈服的那份觉悟，\r\n虽是敌人但真了不起，吾来夸奖你！」",
    "「重傷なのはひとりだけだよ……！」": "「受重伤的只有一个人啊……！」",
    "「じゃがな、調子に乗るでないぞ！\r\nこれはいわば前座！\r\n我らの真の戦いは蕎麦のようなくだらぬものではない！」": "「但是，别得意忘形！\r\n这不过是开场而已！\r\n我们真正的战斗可不是荞麦面这种无聊的东西！」",
    "「あっさり覆したー！」": "「一下子就推翻了——！」",
    "「もぐもぐ、さすがは大妖怪！\r\n機に応じて己の言を翻すとは、\r\n大局をみていらっしゃる、ずずずっ！」": "「嚼嚼，不愧是大妖怪！\r\n能够随机应变改口，\r\n果然是顾全大局啊，呼噜噜——！」",
    "「羊羹食べるか、ヨイショするかどっちかにしたら！？」": "「吃羊羹和拍马屁你选一个行不行！？」",
    "「さあ、鬼鎮めの女よ。\r\nいざ、もう一戦交えようではないか。」": "「来吧，镇鬼之女。\r\n来，让我们再大战一场吧。」",
    "「次はこれにてな！」": "「下一场用这个！」",
    "「あんな大刀を軽々と……！」": "「那么大的刀竟然轻而易举地……！」",
    "「これが鬼の力……！」": "「这就是鬼的力量……！」",
    "「……危ないですよ、街中でそんなものを振り回しては。」": "「……很危险哦，在街上挥舞那种东西。」",
    "「おぬし以外の人間を巻き込むつもりなどない。\r\n用があるのは、にっくき鬼鎮め！」": "「吾无意牵扯你以外的人类。\r\n吾要找的，是可恨的镇鬼！」",
    "「おぬしだけなのだからな！」": "「只有你！」",
    "「……！」": "「……！」",
    "「……？」": "「……？」",
    "「腹が重くて思うように動けぬ。」": "「肚子太沉了，没法随心所欲地动。」",
    "「大剣は支えられるのに自重は無理なの！？」": "「大剑扛得起来自己的体重却不行的吗！？」",
    "「きゃつめ、なんと汚い手を！\r\nわしにたらふく蕎麦を食わせたのは\r\nこのためだったのか！」": "「那家伙，好卑鄙的手段！\r\n让吾吃那么多荞麦面\r\n就是为了这个吗！」",
    "「蕎麦勝負持ち掛けたの自分だよ！」": "「提出荞麦面对决的是你自己啊！」",
    "「くっ、こうなってはしかたあるまい！\r\nこのわしが、本気を出すことになろうとはな。」": "「唔，事到如今也没办法了！\r\n没想到吾竟然要认真起来。」",
    "「本気でございますか……！？\r\nお力を解放すれば多大なる反動があると……！」": "「要认真了吗……！？\r\n如果释放力量的话会有巨大的反噬……！」",
    "「覚悟の上じゃ。」": "「吾已有觉悟。」",
    "「ではどうぞ！」": "「那就请便！」",
    "「どうぞ！」": "「请便！」",
    "「くどい！」": "「啰嗦！」",
    "「……ん？」": "「……嗯？」",
    "「も、もっと止めぬかーっ！\r\n本当にやばいのじゃぞ！\r\nわしの体が大変なことになるのじゃぞ！」": "「你、你倒是拦一下啊——！\r\n真的很危险的啊！\r\n吾的身体会出大事的啊！」",
    "「覚悟の上とおっしゃったので。」": "「因为您说已有觉悟了。」",
    "「阿呆ー！\r\nそこはおぬしが\r\n代わりに戦うと申し出るところじゃろうがー！」": "「笨蛋——！\r\n这种时候你应该\r\n主动请缨代替吾去战斗啊——！」",
    "「鬼の本気……！」": "「鬼的认真……！」",
    "「筆がなるね……！」": "「笔已经跃跃欲试了……！」",
    "「これまでの様子を見てて、\r\nよくそこまで期待できるな！？」": "「看了之前那些表现，\r\n你居然还能抱那么大期待！？」",
    "「みゅ、蕎麦のお姉さん……？」": "「咪嗯，荞麦面姐姐……？」",
    "「あまり、あなどらぬ方がよいようですね。」": "「看来还是不要太小看比较好呢。」",
    "「え？」": "「诶？」",
    "「あと、蕎麦のお姉さんだなんて……、ありがとうございます。\r\n蕎麦好き冥利に尽きます。」": "「还有，叫我荞麦面姐姐什么的……谢谢你。\r\n身为荞麦面爱好者真是太荣幸了。」",
    "「えっ。」": "「诶。」",
    "「グオオオオオッ！」": "「吼哦哦哦哦！」",
    "「なんだ！？」": "「什么！？」",
    "「見るのです、あそこ……！」": "「快看那边……！」",
    "「ひええっ！」": "「呀啊！」",
    "「皆々様、お逃げください……っ！」": "「各位，请快逃……！」",
    "「……くっ、\r\nここから先、お前を通すわけにはいきませぬ……！」": "「……唔，\r\n从这里开始，绝不会让你通过……！」",
    "「モンスター！？」": "「怪物！？」",
    "「女の人がひとりで戦ってるのですよ！」": "「有个女人在独自战斗呢！」",
    "「まずい、早く癒さないと……、」": "「不妙，得赶紧去治愈……」",
    "「なんじゃ、おぬしは！」": "「你是什么东西！」",
    "「グオオオッ！？」": "「吼哦哦！？」",
    "「えっ？」": "「诶？」",
    "「わしの大願の邪魔を……、」": "「胆敢阻碍吾的大愿……」",
    "「するでないわ！」": "「不许！」",
    "「え……、」": "「诶……」",
    "「えーっ！\r\nめちゃくちゃ強いじゃん！\r\nさっきの茶番なんだったんだよ！」": "「诶——！\r\n超级强的啊！\r\n刚才那出闹剧算什么啊！」",
    "「たいてんき様の特技は消化が早いことですから。」": "「因为大天鬼大人的特技是消化很快。」",
    "「腹膨れてただけで弱体化しすぎだろ！」": "「只是吃撑了就虚弱成那样也太夸张了吧！」",
    "「この程度、本気を出すまでもない。\r\n腹ごなしじゃ！\r\nしずめき、おぬしの出る幕はないぞ！」": "「这种程度，不用认真。\r\n就当消食了！\r\n镇鬼，没你上场的份！」",
    "「わきまえております！\r\n窮地に陥り土下座されても、\r\n高みの見物を決め込む所存！」": "「在下明白！\r\n就算您陷入困境跪地求饶，\r\n在下也打算继续袖手旁观！」",
    "「ひでえ。」": "「太过分了。」",
    "「わかっていませんね。\r\nあの程度のモンスター、\r\nたいてんき様にとっては赤子の手を捻るようなもの……、」": "「你们不懂啊。\r\n那种程度的怪物，\r\n对大天鬼大人来说就跟捏死蚂蚁一样……」",
    "「ものすごい顔色悪いけど。」": "「脸色差得吓人啊。」",
    "「食べすぎて腹が痛くなってきた……。」": "「吃太多肚子开始疼了……」",
    "「さすがはたいてんき様！\r\n相手が弱すぎるゆえ、\r\n不利を負わねば戦いを楽しめぬと！」": "「不愧是大天鬼大人！\r\n因为对手太弱了，\r\n不给自己加点不利条件就无法享受战斗！」",
    "「物は言いようにもほどがある！」": "「说话也得有个限度吧！」",
    "「グオオオオッ！」": "「吼哦哦哦！」",
    "「うわっ！」": "「哇！」",
    "「ま、まずいのです！\r\nこのままでは……、」": "「糟、糟糕了！\r\n再这样下去的话……」",
    "「<name>さんたちは下がっていてくれ！」": "「<name>你们先退后！」",
    "「ここは私たちが食い止めてみせます……！」": "「这里由我们来阻止……！」",
    "「えっ！？」": "「诶！？」",
    "「妖怪を追い求める身として、\r\n戦いの術は学んでいるからね！」": "「作为追寻妖怪的人，\r\n战斗的技术还是学过的！」",
    "「はい、私は回復の術を！」": "「是的，我会恢复术！」",
    "「そうだったのですね！\r\n心強いのですよ～！」": "「原来如此呢！\r\n真令人安心～！」",
    "「わたくしもお力添えをいたしましょう。\r\n微々たるものではありますが、\r\nこれでも鬼鎮めの家に生まれた者ですから。」": "「我也来帮忙吧。\r\n虽然微不足道，\r\n但好歹也是出生于镇鬼之家的人。」",
    "「蕎麦のお姉さん！」": "「荞麦面姐姐！」",
    "「頼りになるのですよ！\r\nこれならきっと……！」": "「真可靠呢！\r\n这样的话一定……！」",
    "「それでは、たいてんきさん！」": "「那么，大天鬼先生！」",
    "「鬼の力、存分に振るってくれたまえ！」": "「请尽情发挥鬼的力量吧！」",
    "「なんでじゃー！\r\nそこはおぬしらが力を合わせて\r\nこやつを撃退するところじゃろうがー！」": "「为什么啊——！\r\n这种时候应该你们齐心协力\r\n把这家伙击退才对啊——！」",
    "「え？\r\nなぜって……、」": "「诶？\r\n说为什么……」",
    "「僕たちみんな……、」": "「因为我们都……」",
    "「後衛だから！」": "「是后卫啊！」",
    "「お……、」": "「你、你们……」",
    "「おぬしら、あとで覚えておれよー！」": "「你们给吾记住——！」",
}

# Merge all translations
ALL_TRANSLATIONS = {}
ALL_TRANSLATIONS.update(SPEAKER_TRANSLATIONS)
ALL_TRANSLATIONS.update(DIALOGUE_TRANSLATIONS)

# Load and process
BUNDLE_ORIG = r"D:\cs\workshop\eb777f2829400cfced05a3761d77fd6a.bundle.original"
STORY_DIR = r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"
BUNDLE_DEST = os.path.join(STORY_DIR, "eb777f2829400cfced05a3761d77fd6a.bundle")

env = UnityPy.load(BUNDLE_ORIG)
for obj in env.objects:
    if obj.type.name == "TextAsset":
        obj.reset()
        reader = obj.reader
        reader.Position = obj.byte_start
        raw = reader.read(obj.byte_size)

        pos = 0
        name_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
        name_bytes = raw[pos:pos+name_len]; pos += name_len
        pos = (pos + 3) & ~3
        script_len = struct.unpack_from('<i', raw, pos)[0]; pos += 4
        encrypted_data = raw[pos:pos+script_len]

        pt = decrypt(encrypted_data)
        print(f"Decrypted: {len(pt)} bytes")

        # Apply ALL translations globally
        print("\nApplying translations:")
        modified = replace_all_mempack_strings(pt, ALL_TRANSLATIONS)
        print(f"\nModified: {len(pt)} -> {len(modified)} bytes")

        # Re-encrypt
        new_encrypted = encrypt(modified)
        check = decrypt(new_encrypted)
        assert check == modified, "Encryption round-trip failed!"

        # Build new raw TextAsset
        new_raw = bytearray()
        new_raw.extend(struct.pack('<i', len(name_bytes)))
        new_raw.extend(name_bytes)
        while len(new_raw) % 4 != 0:
            new_raw.append(0)
        new_raw.extend(struct.pack('<i', len(new_encrypted)))
        new_raw.extend(new_encrypted)

        # Repack
        env2 = UnityPy.load(BUNDLE_ORIG)
        for obj2 in env2.objects:
            if obj2.type.name == "TextAsset":
                obj2.set_raw_data(bytes(new_raw))

        with open(BUNDLE_DEST, 'wb') as f:
            f.write(env2.file.save())

        print(f"\nInstalled to: {BUNDLE_DEST}")
        print(f"Size: {os.path.getsize(BUNDLE_DEST)} bytes")

        # Quick verification - check speaker names are translated
        env3 = UnityPy.load(BUNDLE_DEST)
        for obj3 in env3.objects:
            if obj3.type.name == "TextAsset":
                obj3.reset()
                r3 = obj3.reader
                r3.Position = obj3.byte_start
                raw3 = r3.read(obj3.byte_size)
                p = 0
                nl = struct.unpack_from('<i', raw3, p)[0]; p += 4
                p += nl; p = (p + 3) & ~3
                sl = struct.unpack_from('<i', raw3, p)[0]; p += 4
                pt3 = decrypt(raw3[p:p+sl])

                # Check for remaining Japanese speaker names
                remaining_jp = []
                for jp_name in SPEAKER_TRANSLATIONS:
                    if jp_name.encode('utf-8') in pt3:
                        remaining_jp.append(jp_name)

                if remaining_jp:
                    print(f"\nWARNING: Some JP speaker names still present: {remaining_jp}")
                else:
                    print("\nAll speaker names translated!")

                # Check Chinese names present
                for cn_name in SPEAKER_TRANSLATIONS.values():
                    if cn_name.encode('utf-8') in pt3:
                        count = pt3.count(cn_name.encode('utf-8'))
                        print(f"  '{cn_name}': {count} occurrences")

print("\nDone! Launch the game and check story 1621.")

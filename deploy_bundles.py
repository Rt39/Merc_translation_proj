"""Deploy repacked bundles to CDN cache, overwriting originals.
Backs up originals to .bak before overwriting.
"""
import shutil, os

CACHE_STORY = (
    r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements"
    r"\メルストM\AssetBundle\StandaloneWindows64\StoryMasterData"
)
CACHE_MASTER = (
    r"C:\Users\hwwys\AppData\LocalLow\jp_co_happyelements"
    r"\メルストM\AssetBundle\StandaloneWindows64\MasterData"
)
REPACKED_STORY = r"D:\cs\workshop\repacked_bundles\story"
REPACKED_MISC = r"D:\cs\workshop\repacked_bundles\misc"

count_story = 0
count_misc = 0

print("Deploying story bundles to CDN cache...")
for fname in os.listdir(REPACKED_STORY):
    src = os.path.join(REPACKED_STORY, fname)
    dst = os.path.join(CACHE_STORY, fname)
    bak = dst + ".bak"
    
    # Back up original if not already backed up
    if os.path.exists(dst) and not os.path.exists(bak):
        shutil.copy2(dst, bak)
    
    shutil.copy2(src, dst)
    count_story += 1

print(f"  {count_story} story bundles deployed")

print("Deploying misc bundles to CDN cache...")
for fname in os.listdir(REPACKED_MISC):
    src = os.path.join(REPACKED_MISC, fname)
    dst = os.path.join(CACHE_MASTER, fname)
    bak = dst + ".bak"
    
    if os.path.exists(dst) and not os.path.exists(bak):
        shutil.copy2(dst, bak)
    
    shutil.copy2(src, dst)
    count_misc += 1

print(f"  {count_misc} misc bundles deployed")

print(f"\nDeployed {count_story + count_misc} bundles total.")
print(f"Originals backed up to *.bak in the same folders.")

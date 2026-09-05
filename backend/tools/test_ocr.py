"""Live check: OCR the cash HUD and any visible priority-stat price box."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from device import capture
from vision import ocr
from interactions import shopper
import cv2

frame = capture.grab()
cash_crop = capture.roi(frame, "cash_box")
cv2.imwrite("captures/ocr_cash.png", cash_crop)
print("cash:", ocr.read_amount(cash_crop))

STATS = ["damage", "health", "health_regen", "critical_factor",
         "damage_per_meter", "enemy_attack_level_skip", "death_defy",
         "land_mine_radius", "bounce_shot_range"]
for stat in STATS:
    center, buyable, price = shopper._find_stat(frame, stat)
    if center is not None:
        print(f"{stat}: buyable={buyable} price={price}")

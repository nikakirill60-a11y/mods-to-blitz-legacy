#!/usr/bin/env python3
"""Generate the TankInfoMain resource of the MS-11 (МС-11) and its drum test variants.

The MS-11 has an 11-shell drum: 11 shots 0.8 s apart, then a 22.05 s magazine
reload.  How the engine describes a magazine is NOT documented anywhere this
repository could find:

  * every gun of the reference mods carries an (always empty) `burst_info`
    sub-resource - script res://assets/data/tank_info/burst_info.gd,
  * the double-barrelled Mäuschen (germany_mauses) is the only gun with a filled
    `cycle_info` (reload / intrashot / volley_charge_time) and `fire_mode = 3`.
    Its cycle is two shots - one per muzzle, `gun01_shoot_posL` / `...R`.

So the field holding the number of shells and the `fire_mode` value of a
magazine gun are a best guess.  Godot does not fail on unknown properties of a
scripted resource (they are kept as "missing" properties and ignored), so the
shell count is written under every plausible name at once, in both burst_info
and cycle_info.  The timing uses the names known from cycle_info.

Output:
  tanks_mod/ussr_ms11.tres               main file: fire_mode = 2 (cycle without
                                         the Mäuschen's volley)
  variants/fire_mode_1/ussr_ms11.tres    same drum, fire_mode = 1
  variants/fire_mode_3/ussr_ms11.tres    same drum, fire_mode = 3 (Mäuschen mode)
  variants/no_drum/ussr_ms11.tres        no drum at all: a single-shot gun with the
                                         same damage per minute (fallback in case a
                                         drum variant does not load)

Usage:  python3 mods/ussr_ms11/tools/make_tres.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
MOD = os.path.join(ROOT, "mods", "ussr_ms11")

# --- drum (confirmed: ru wiki, update 12.6) -------------------------------------------
DRUM_SHELLS = 11
DRUM_INTRASHOT = 0.8      # s between shots inside the drum
DRUM_RELOAD = 22.05       # s to reload the whole drum
SHELL_DMG_AP = 140

# The same damage per minute as the drum: 11 shells per (22.05 + 10 * 0.8) s.
CYCLE_TIME = DRUM_RELOAD + (DRUM_SHELLS - 1) * DRUM_INTRASHOT
NO_DRUM_RELOAD = round(CYCLE_TIME / DRUM_SHELLS, 2)

COUNT_FIELDS = ["count", "shots", "shell_count", "shells_count", "clip_size", "clip",
                "size", "amount", "burst_count", "shots_count"]

HEADER = """[gd_resource type="Resource" script_class="TankInfoMain" format=3 uid="uid://{uid}"]

[ext_resource type="Script" uid="uid://ik8cl56b1kn8" path="res://assets/data/tank_info/module_engine.gd" id="1_ms11e"]
[ext_resource type="Script" uid="uid://cwn7pqhusf5ai" path="res://assets/data/tank_info/module_gun.gd" id="2_ms11g"]
[ext_resource type="Script" uid="uid://db3udi6hv6vml" path="res://assets/data/tank_info/burst_info.gd" id="3_ms11b"]
[ext_resource type="Script" uid="uid://b3ngpj6cakj2c" path="res://assets/data/tank_info/cycle_info.gd" id="4_ms11c"]
[ext_resource type="Script" uid="uid://bmjo6p14kkiqq" path="res://assets/data/tank_info/shell_info.gd" id="5_ms11s"]
[ext_resource type="Script" uid="uid://dqiabdd87qy5b" path="res://assets/data/tank_info/tank_info.gd" id="6_ms11t"]
[ext_resource type="Script" uid="uid://c47xj1fnl8h2t" path="res://assets/data/tank_info/module_tracks.gd" id="7_ms11k"]

[sub_resource type="Resource" id="Resource_engine"]
script = ExtResource("1_ms11e")
power = 900
fireStartingChance = 0.12
name = "В-2-МС"
display_name = "В-2-МС"
level = 10
health = 400
weight = 1100
"""

BURST_DRUM = """
[sub_resource type="Resource" id="Resource_burst"]
script = ExtResource("3_ms11b")
{counts}
intrashot = {intrashot}
interval = {intrashot}
reload = {reload}
"""

BURST_EMPTY = """
[sub_resource type="Resource" id="Resource_burst"]
script = ExtResource("3_ms11b")
"""

CYCLE_DRUM = """
[sub_resource type="Resource" id="Resource_cycle"]
script = ExtResource("4_ms11c")
reload = {reload}
intrashot = {intrashot}
{counts}
"""

BODY = """
[sub_resource type="Curve" id="Curve_deviation_up"]

[sub_resource type="Curve" id="Curve_deviation_down"]

[sub_resource type="Resource" id="Resource_shell_ap"]
script = ExtResource("5_ms11s")
name = "БР-85МС"
dmg = 140
pen = 244
caliber = 85
speed = 1000
type = 0
cost = 290

[sub_resource type="Resource" id="Resource_shell_apcr"]
script = ExtResource("5_ms11s")
name = "БП-85МС"
dmg = 115
pen = 320
caliber = 85
speed = 1250
type = 1
cost = 1400
premium = true

[sub_resource type="Resource" id="Resource_shell_he"]
script = ExtResource("5_ms11s")
name = "ОФ-85МС"
dmg = 200
pen = 50
caliber = 85
speed = 800
type = 3
cost = 250

[sub_resource type="Resource" id="Resource_gun"]
script = ExtResource("2_ms11g")
reload = {gun_reload}
disp = 0.36
aim_time = 2.0
deviation_up = Vector2(15, 10)
deviation_up_custom = SubResource("Curve_deviation_up")
deviation_down_custom = SubResource("Curve_deviation_down")
caliber = 85
shot_sound = 3
{fire_mode}rot_speed = 36
max_ammo = 66
burst_info = SubResource("Resource_burst")
{cycle}shells = Array[ExtResource("5_ms11s")]([SubResource("Resource_shell_ap"), SubResource("Resource_shell_apcr"), SubResource("Resource_shell_he")])
name = "{gun_name}"
display_name = "{gun_name}"
level = 10
health = 220
weight = 2500

[sub_resource type="Resource" id="Resource_tracks"]
script = ExtResource("7_ms11k")
rot_speed = 61
name = "МС-11"
display_name = "МС-11"
level = 10
health = 280
weight = 12000

[resource]
script = ExtResource("6_ms11t")
icon = "res://assets/tanks/ussr_ms11_icon.png"
tex_main = "res://assets/tanks/ussr_ms11.png"
tex_tracks = "res://assets/tanks/ussr_ms11_track.png"
model_path = "res://assets/tanks/ussr_ms11.glb"
name = "ussr_ms11"
display_name = "МС-11"
display_name_short = "МС-11"
health = 1910
lvl = 10
farm_coeff = 4.0
tank_class = 1
nation = 1
speed_limits = Vector2(55, 20)
turret_rot_speed = 0.8028514559173916
turret_health = 160.0
tree_tank = true
tree_branch = 2
prem = true
cost_xp = 0
cost = 15000
hull_weight = 36000
engine_sound = 2
track_sound = 2
matchmaker_chance = 100.0
guns = Array[ExtResource("2_ms11g")]([SubResource("Resource_gun")])
engines = Array[ExtResource("1_ms11e")]([SubResource("Resource_engine")])
tracks = Array[ExtResource("7_ms11k")]([SubResource("Resource_tracks")])
"""


def build(fire_mode, drum, uid):
    counts = "\n".join("%s = %d" % (field, DRUM_SHELLS) for field in COUNT_FIELDS)
    text = HEADER.format(uid=uid)
    if drum:
        text += BURST_DRUM.format(counts=counts, intrashot=DRUM_INTRASHOT, reload=DRUM_RELOAD)
        text += CYCLE_DRUM.format(counts=counts, intrashot=DRUM_INTRASHOT, reload=DRUM_RELOAD)
    else:
        text += BURST_EMPTY
    text += BODY.format(
        gun_reload=DRUM_RELOAD if drum else NO_DRUM_RELOAD,
        fire_mode=("fire_mode = %d\n" % fire_mode) if drum else "",
        cycle='cycle_info = SubResource("Resource_cycle")\n' if drum else "",
        gun_name="85 мм МС-11 (барабан 11)" if drum else "85 мм МС-11",
    )
    return text


def write(rel_path, text):
    path = os.path.join(MOD, rel_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("written %s" % os.path.relpath(path, ROOT))


def main():
    # The uid of the resource is the same in every variant: they all replace the same
    # file (tanks_mod/ussr_ms11.tres), only one of them is installed at a time.
    uid = "1ms11drum11xa"
    write("tanks_mod/ussr_ms11.tres", build(2, True, uid))
    write("variants/fire_mode_1/ussr_ms11.tres", build(1, True, uid))
    write("variants/fire_mode_3/ussr_ms11.tres", build(3, True, uid))
    write("variants/no_drum/ussr_ms11.tres", build(0, False, uid))
    print("drum: %d x %d dmg, %.2f s between shots, %.2f s reload -> cycle %.2f s, "
          "DPM %.0f; no-drum reload %.2f s (DPM %.0f)"
          % (DRUM_SHELLS, SHELL_DMG_AP, DRUM_INTRASHOT, DRUM_RELOAD, CYCLE_TIME,
             DRUM_SHELLS * SHELL_DMG_AP * 60.0 / CYCLE_TIME, NO_DRUM_RELOAD,
             SHELL_DMG_AP * 60.0 / NO_DRUM_RELOAD))
    return 0


if __name__ == "__main__":
    sys.exit(main())

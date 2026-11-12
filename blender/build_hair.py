"""
Cherry wolf-cut + hime-cut hair for Roblox (UGC rigid accessory, Hair, R15 Classic scale).

Run headless:   blender -b -P blender/build_hair.py          (Blender 4.x)
           or:  python blender/build_hair.py                  (pip `bpy` module)
Run in the UI:  Scripting tab > open this file > Run Script   (use a new, empty .blend)
Options (after `--` when run through blender):
    --out DIR        write the FBX / textures / .blend there instead of ./export
    --keep-curves    keep the curves already in the .blend (hand edits) and only redo stages 3-7

Stages: 0 reference avatar > 1 blocking > 2 curves (Bezier + bevel profile + taper + fill caps)
        > 3 convert + clean > 4 UV > 5 texture > 6 checks > 7 FBX + .blend
1 Blender unit = 1 stud, avatar faces -Y, character's left = +X.
"""
import math
import os
import random
import sys
import urllib.request

import bpy  # first: the pip `bpy` module only exposes bmesh / mathutils once it is loaded
import bmesh
import numpy as np
from mathutils import Vector

NAME = "CherryWolfHime_Hair"
SEED = 11
TRI_LIMIT = 4000
TEX_SIZE = 1024
NORMAL_SIZE = 256            # Roblox budget for non-albedo maps on rigid accessories
COL_MAIN, COL_ROOT, COL_HI = "#7E2031", "#3E1119", "#A14A5C"
# Classic "Hair" bounds in studs, measured from the HairAttachment.
LIMITS = dict(side=1.5, up=2.0, down=3.0, front=1.5, back=2.0)
REF_URL = ("https://prod.docsiteassets.roblox.com/assets/avatar/dynamic-heads/"
           "reference-files/BlockyCharacter.fbx")

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _HERE = ""
if not os.path.isdir(_HERE):                        # run from a text block inside a .blend
    _HERE = os.path.dirname(bpy.data.filepath) or os.getcwd()
ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "blender" else _HERE
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OUT = os.path.abspath(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else os.path.join(ROOT, "export")
CACHE = os.path.join(ROOT, ".cache")


class Ref:
    """Head metrics of the official R15 blocky character (overwritten from the imported file)."""
    head_c = Vector((0.0, 0.0, 2.537))
    att = Vector((0.0, 0.0, 3.144))


# ── hair volume: superellipsoid around the head (the "blocking" silhouette) ─────────────
AX, AY, BZ, CY, PEXP = 0.79, 0.775, 0.84, 0.035, 2.2
WHORL_BACK = 14.0            # crown whorl sits this many degrees behind the top of the head
U_HALO = 42.0                # latitude of the gloss ring, degrees from the top
T_HALO = 0.34                # where that ring is painted along a strand's texture (0 root, 1 tip)


def dome_c():
    return Ref.head_c + Vector((0.0, CY, 0.0))


def ang_dir(phi, u):
    """Unit direction: phi = azimuth from the front (+ = character's left), u = angle from the top."""
    p, uu = math.radians(phi), math.radians(u)
    return Vector((math.sin(uu) * math.sin(p), -math.sin(uu) * math.cos(p), math.cos(uu)))


def on_dome(d, lift=1.0):
    d = Vector(d).normalized()
    f = ((d.x / AX) ** 2 + (d.y / AY) ** 2) ** (PEXP / 2) + abs(d.z / BZ) ** PEXP
    return dome_c() + d * (lift * f ** (-1.0 / PEXP))


def u_for_z(phi, z, lift):
    lo, hi = 20.0, 130.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if on_dome(ang_dir(phi, mid), lift).z - Ref.head_c.z > z:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def e_phi(phi):
    p = math.radians(phi)
    return Vector((math.cos(p), math.sin(p), 0.0))


# Control points are (position, kind, hint): kind 'n' = outward normal hint (lying on the head),
# kind 'e' = width direction (free-hanging, keeps the outer face continuous through flips).
def dpt(d, lift=1.0):
    p = on_dome(d, lift)
    return (p, "n", (p - dome_c()).normalized())


def fpt(phi, q, z, lift=1.0):
    p = math.radians(phi)
    pos = Ref.head_c + Vector((AX * q * lift * math.sin(p), CY - AY * q * lift * math.cos(p), z))
    return (pos, "e", e_phi(phi))


def apt(xyz, frame_phi):
    return (Ref.head_c + Vector(xyz), "e", e_phi(frame_phi))


# ── curve tools: bevel profiles (unit width, +y = outer side) and taper curves ───────────
class Profile:
    def __init__(self, pts, n_top):
        self.pts, self.n = pts, len(pts)
        self.iL, self.iR = 0, n_top
        self.top = list(range(1, n_top))
        # per profile edge: (region, u at its first vertex, u at its second vertex)
        self.segs = []
        for k in range(self.n):
            x0, x1 = pts[k][0], pts[(k + 1) % self.n][0]
            self.segs.append(("top", 0.5 - x0, 0.5 - x1) if k < n_top else ("under", 0.5 + x0, 0.5 + x1))


PROFILES = {
    "diamond": Profile([(-.5, 0), (0, .15), (.5, 0), (0, -.07)], 2),
    "tri": Profile([(-.5, 0), (0, .19), (.5, 0)], 2),
    "chunk": Profile([(-.5, 0), (0, .24), (.5, 0), (0, -.12)], 2),
    "arch": Profile([(-.5, 0), (-.2, .13), (.2, .13), (.5, 0), (0, -.07)], 3),
    "band": Profile([(-.5, 0), (-.27, .11), (.27, .11), (.5, 0), (.27, -.045), (-.27, -.045)], 3),
}
TAPERS = {   # (position along the strand 0..1, width factor)
    "petal": [(0, .30), (.125, .58), (.25, .85), (.375, 1.0), (.75, .9), (1, 0)],
    "lock": [(0, .30), (.125, .58), (.25, .85), (.375, 1.0), (.85, .95), (1, 0)],
    "inner": [(0, .7), (.3, 1.0), (.65, .8), (1, 0)],
    "flip": [(0, .5), (.2, 1.0), (.7, .95), (1, 0)],
    "wisp": [(0, .6), (.15, 1.0), (.5, .8), (1, 0)],
    "fly": [(0, .3), (.4, 1.0), (1, 0)],
}


class Spec:
    def __init__(self, name, group, pts, width, taper, profile="diamond", res=2, slot=0,
                 crown=False, striped=False, lit_under=False):
        self.name, self.group, self.pts, self.width = name, group, pts, width
        self.taper, self.profile, self.res, self.slot = taper, profile, res, slot
        self.crown, self.striped, self.lit_under = crown, striped, lit_under


def build_specs():
    """Strand layout. Each tier is defined for one side, mirrored across X, then de-symmetrised."""
    rng = random.Random(SEED)
    j = lambda a: rng.uniform(-a, a)                               # noqa: E731
    W = ang_dir(180.0, WHORL_BACK)
    S = []

    def crown(phi, u_exit, lift, t0=0.02, root_lift=None):
        ex = ang_dir(phi, u_exit)
        r0 = lift if root_lift is None else root_lift          # upper layers still start in the whorl
        return [dpt(W.slerp(ex, t), lf) for t, lf in ((t0, r0), (0.5, (r0 + 2 * lift) / 3), (1.0, lift))]

    def sides(phi):
        return (1,) if phi in (0, 180) else (1, -1)

    def tag(phi, sd, i):
        return f"{'C' if phi in (0, 180) else ('L' if sd > 0 else 'R')}{i}"

    def hang(x, y, z0, zt, n, frame_phi, amp=.03, pull=.12, dy=-.05):
        """Free-hanging tail from shoulder height to a sharp tip, with a slight S-wave."""
        ph = rng.random()
        out = []
        for k in range(n):
            f = k / (n - 1)
            wave = amp * f * math.sin(math.pi * (1.4 * f + ph))
            out.append(apt((x * (1 - pull * f) + wave, y + dy * f, z0 + (zt - z0) * f), frame_phi))
        return out

    # 1. Bangs: thick, choppy, pointed, down to the eyebrows (eyes top out at z = +0.22).
    for i, (phi, ztip, w, slot) in enumerate([(0, .30, .27, 2), (13.5, .335, .25, 0),
                                              (27, .275, .25, 1), (40.5, .20, .24, 0)]):
        for sd in sides(phi):
            p, lift = phi * sd + j(1.5), 1.0 + 0.012 * (i % 2)
            ut = u_for_z(p, ztip + j(.02), lift)
            pts = crown(p, 50, lift)
            pts += [dpt(ang_dir(p + j(1.5), (50 + ut) / 2), lift + .008),
                    dpt(ang_dir(p + j(2.5), ut), lift + .03)]
            S.append(Spec("Bang_" + tag(phi, sd, i), "Bangs", pts, w * (1 + j(.05)), "petal", "arch",
                          slot=slot, crown=True))
    for i, (phi, ztip) in enumerate([(7, .37), (20.5, .345), (34, .31)]):      # under-layer
        for sd in (1, -1):
            p, lift = phi * sd + j(1.5), 0.97
            ut = u_for_z(p, ztip + j(.02), lift)
            pts = [dpt(ang_dir(p, 49), lift), dpt(ang_dir(p, (49 + ut) / 2), lift),
                   dpt(ang_dir(p + j(2), ut), lift + .01)]
            S.append(Spec("BangIn_" + tag(phi, sd, i), "Bangs", pts, .27, "inner", slot=3))

    # 2. Side locks (hime): straight, flat, framing the face; the chin sits level with the shoulders,
    #    so they stop just above the torso (top at z = -0.54) instead of clipping into it.
    for i, (phi, ztip, w, lift) in enumerate([(54, -.505, .30, 1.0), (73, -.45, .26, .985)]):
        for sd in (1, -1):
            p = phi * sd + j(1.0)
            pts = crown(p, 80, lift)
            pts += [fpt(p + j(.8), .985, -.25, lift), fpt(p + j(1.2), .955, ztip + j(.015), lift)]
            S.append(Spec("Side_" + tag(phi, sd, i), "Sides", pts, w, "lock", "arch", slot=i, crown=True))

    # 3. Crown layer (wolf cut): short choppy layer, ends kick out between cheek and jaw.
    for i, (phi, ztip, kick) in enumerate([(82, -.20, 1.26), (105, -.36, 1.22), (128, -.16, 1.14),
                                           (151, -.40, 1.13), (172, -.24, 1.12)]):
        for sd in (1, -1):
            p, lift = phi * sd + j(4), 1.015 + 0.008 * (i % 2)
            pts = crown(p, 80, lift)
            pts += [fpt(p + j(2), 1.03, -.06, lift),
                    fpt(p + j(4), kick + j(.04), ztip + j(.06), lift)]
            S.append(Spec("Crown_" + tag(phi, sd, i), "Crown", pts, .37 * (1 + j(.10)), "petal", "arch",
                          slot=(0, 1, 2)[i % 3], crown=True))
    for i, phi in enumerate([94, -93, 140, 118, -139]):            # thin stray flicks between them
        p = phi + j(3)
        pts = [dpt(ang_dir(p, 55), .995), fpt(p + j(3), 1.06, -.04 + j(.04)),
               fpt(p + j(6), 1.22 + j(.04), -.28 + j(.08))]
        S.append(Spec(f"Wisp_{i}", "Crown", pts, .13, "fly", "chunk", 2, slot=2, lit_under=True))

    # 4. Shoulder layer: chunky tips flipped outward just above the shoulders.
    for i, phi in enumerate([70, 93.5, 116.5, 139.5, 162, 180]):
        for sd in sides(phi):
            p, lift, d = phi * sd + j(3), .97, j(4)
            flip = .30 + j(.06)
            pts = [dpt(ang_dir(p, 84), lift), fpt(p + d * .5, 1.01, -.30, lift),
                   fpt(p + d, 1.14, -.46, lift), fpt(p + d * 1.5, 1.14 + flip, -.40 + j(.03), lift)]
            S.append(Spec("Flip_" + tag(phi, sd, i), "Flips", pts, .30 * (1 + j(.08)), "flip", "chunk",
                          slot=4))

    # 5. Long layers: sharp tips at the waist. Chest strands hug the torso (front face y = -0.5).
    for sd in (1, -1):
        for i, (phi, x, ztip, w, n, slot) in enumerate([(52, .50, -1.98, .26, 5, 1),
                                                        (61, .66, -1.72, .22, 5, 0),
                                                        (66, .78, -1.42, .14, 4, 2),
                                                        (57, .58, -1.12, .13, 3, 0)]):
            pts = [dpt(ang_dir(phi * sd, 64), .965), dpt(ang_dir((phi - 2) * sd, 90), .975)]
            pts += hang(sd * (x + j(.02)), -.575, -.50, ztip + j(.06), n, sd * 18,
                        amp=.025, pull=.2, dy=-.012)
            S.append(Spec(f"LongFront_{'L' if sd > 0 else 'R'}{i}", "Long", pts, w, "wisp", "tri", 1, slot=slot))
    back = lambda x: (180 - x / .85 * 68, .60 + .17 * math.cos(x / .85 * math.pi / 2),   # noqa: E731
                      180 - x / .85 * 30)
    for i, (x, ztip) in enumerate(zip([-.78, -.52, -.26, 0, .26, .52, .78],
                                      [-1.85, -2.20, -1.70, -2.25, -1.95, -2.15, -1.75])):
        phi, y, fp = back(x)
        pts = [dpt(ang_dir(phi, 66), .96), dpt(ang_dir(phi, 91), .975)]
        pts += hang(x + j(.03), y, -.50, ztip + j(.05), 5, fp)
        S.append(Spec(f"LongBack_{i}", "Long", pts, .32 * (1 + j(.08)), "wisp", "tri", 1, slot=5))
    for i, (x, ztip, w) in enumerate([(-.66, -1.55, .15), (-.38, -2.28, .13), (.12, -1.45, .14),
                                      (.40, -2.05, .13), (.67, -2.27, .15)]):
        phi, y, fp = back(x)
        pts = [dpt(ang_dir(phi, 78), 1.0)] + hang(x, y + .06, -.45, ztip, 4, fp, amp=.05)
        S.append(Spec(f"LongWisp_{i}", "Long", pts, w, "wisp", "tri", 1, slot=2))

    # 6. A couple of loose flyaways on the crown.
    for i, (phi, lifts, ts, dp) in enumerate([(-62, (.995, 1.045, 1.07), (.16, .50, .84), 10),
                                              (148, (.995, 1.045, 1.065), (.14, .45, .72), -8)]):
        pts = [dpt(W.slerp(ang_dir(phi + dp * k / 2, 68), t), lf)
               for k, (t, lf) in enumerate(zip(ts, lifts))]
        S.append(Spec(f"Loose_{i}", "Loose", pts, .13, "fly", "chunk", 2, slot=0, lit_under=True))

    # 7. Accent: tiger-striped strand, character's LEFT (+X), crown to cheekbone, on top of all.
    lift = 1.07
    pts = crown(47, 70, lift, root_lift=1.0)
    pts += [fpt(46.5, .995, .05, lift), fpt(46, .985, -.15, lift)]
    S.append(Spec("Striped_L", "Striped", pts, .25, "lock", "band", 2, crown=True, striped=True))
    pts = crown(-47, 70, 1.045, root_lift=1.0)                     # plain twin framing the right temple
    pts += [fpt(-46.5, .995, .05, 1.045), fpt(-45, .985, -.10, 1.045)]
    S.append(Spec("Temple_R", "Sides", pts, .24, "lock", "arch", 2, slot=2, crown=True))
    return S


# ── stage 0: reference avatar ────────────────────────────────────────────────────────────
def new_collection(name, parent=None, hide_render=False):
    c = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(c)
    c.hide_render = hide_render
    return c


def flat_material(name, rgb, rough=0.6):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = rough
    m.diffuse_color = (*rgb, 1.0)
    return m


def _components(bm):
    seen, comps = set(), []
    for v in bm.verts:
        if v in seen:
            continue
        stack, comp = [v], []
        seen.add(v)
        while stack:
            a = stack.pop()
            comp.append(a)
            for e in a.link_edges:
                b = e.other_vert(a)
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        comps.append(comp)
    return comps


HEAD_PROFILE = [(.42, -.601), (.515, -.55), (.578, -.45), (.6, -.33), (.6, .33), (.578, .45),
                (.514, .55), (.42, .601)]            # (radius, z) of the classic head, for offline use


def load_reference(coll):
    """Official Roblox R15 blocky character (1 unit = 1 stud); primitive stand-in if offline.

    Body and eyes are the official meshes. The animatable head's shell has an open mouth cavity,
    so it is swapped for a clean lathe of its own silhouette (same size, measured from the file).
    """
    path = os.path.join(CACHE, "BlockyCharacter.fbx")
    if not os.path.exists(path):
        try:
            os.makedirs(CACHE, exist_ok=True)
            urllib.request.urlretrieve(REF_URL, path)
        except Exception as exc:                                   # noqa: BLE001
            print(f"    reference download failed ({exc}); using a primitive stand-in")
            path = None
    bm = bmesh.new()
    mat = bm.faces.layers.int.new("mat")
    prof = HEAD_PROFILE
    if path:
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=path)
        new = [o for o in bpy.data.objects if o not in before]
        att = next((o for o in new if o.name.startswith("Hair_Att")), None)
        if att:
            Ref.att = att.matrix_world.translation.copy()
        head = []
        for o in new:
            if o.type == "MESH" and o.name.split(".")[0].endswith("_Geo"):
                start = len(bm.verts)
                bm.from_mesh(o.data)
                bm.verts.ensure_lookup_table()
                part = bm.verts[start:]
                for v in part:
                    v.co = o.matrix_world @ v.co
                if o.name.startswith("Head_Geo"):
                    head = part
        for o in new:
            bpy.data.objects.remove(o, do_unlink=True)
        co = np.array([v.co[:] for v in head])
        Ref.head_c = Vector((0.0, 0.0, 0.5 * (co[:, 2].min() + co[:, 2].max())))
        rad, zz = np.hypot(co[:, 0], co[:, 1]), co[:, 2] - Ref.head_c.z
        cuts = np.linspace(zz.min(), zz.max(), 31)
        prof = [(float(rad[zz < cuts[0] + .004].max()), float(cuts[0]))]
        prof += [(float(rad[(zz >= a) & (zz <= b)].max()), float(a + b) / 2)
                 for a, b in zip(cuts, cuts[1:]) if ((zz >= a) & (zz <= b)).any()]
        prof.append((float(rad[zz > cuts[-1] - .004].max()), float(cuts[-1])))
        head_set, doomed = set(head), set()
        for comp in _components(bm):
            if comp[0] not in head_set:
                continue
            faces = {f for v in comp for f in v.link_faces}
            centre = sum((v.co for v in comp), Vector()) / len(comp)
            if len(comp) < 250 and centre.z > Ref.head_c.z:         # eyes: keep, proud of the lathe
                for v in comp:
                    v.co += Vector((v.co.x, v.co.y, 0.0)).normalized() * .008
                for f in faces:
                    f[mat] = 1
            else:                                                  # shell, teeth, tongue
                doomed |= faces
        bmesh.ops.delete(bm, geom=list(doomed), context="FACES")
    else:
        for (sx, sy, sz), (cx, cy, cz) in (((2, 1, 2), (0, 0, 1)), ((1, 1, 2), (1.5, 0, 1)),
                                           ((1, 1, 2), (-1.5, 0, 1)), ((1, 1, 2), (.5, 0, -1)),
                                           ((1, 1, 2), (-.5, 0, -1))):
            for v in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
                v.co = Vector((v.co.x * sx + cx, v.co.y * sy + cy, v.co.z * sz + cz))
    hc, seg = Ref.head_c, 32
    rings = [[bm.verts.new(hc + Vector((r * math.sin(a), -r * math.cos(a), z)))
              for a in (2 * math.pi * k / seg for k in range(seg))] for r, z in prof]
    bm.faces.new(rings[0])
    bm.faces.new(rings[-1])
    for a, b in zip(rings, rings[1:]):
        for k in range(seg):
            bm.faces.new((a[k], a[(k + 1) % seg], b[(k + 1) % seg], b[k]))
    cols = []                                                      # a simple smile
    for k in range(9):
        x = -.17 + .34 * k / 8
        y, dz = -math.sqrt(.607 ** 2 - x * x), .055 * (x / .17) ** 2
        cols.append((bm.verts.new(hc + Vector((x, y, -.262 + dz))),
                     bm.verts.new(hc + Vector((x, y, -.232 + dz)))))
    for a, b in zip(cols, cols[1:]):
        bm.faces.new((a[0], b[0], b[1], a[1]))[mat] = 1
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new("REF_R15_Blocky")
    for f in bm.faces:
        f.material_index = f[mat]
        f.smooth = len(f.verts) <= 4
    for e in bm.edges:
        e.smooth = len(e.link_faces) == 2 and e.calc_face_angle() < math.radians(40)
    bm.faces.layers.int.remove(mat)
    bm.to_mesh(me)
    bm.free()
    if hasattr(me, "use_auto_smooth"):              # Blender 4.0
        me.use_auto_smooth = True
        me.auto_smooth_angle = math.pi
    me.materials.append(flat_material("REF_Body", (.80, .78, .76)))
    me.materials.append(flat_material("REF_Face", (.03, .03, .035)))
    ob = bpy.data.objects.new(me.name, me)
    coll.objects.link(ob)
    ob.hide_select = True
    return ob


# ── stage 1: blocking ────────────────────────────────────────────────────────────────────
def build_blocking(coll):
    """Wire guides: the hair volume the strands are laid on, and Roblox's size-limit box."""
    bm = bmesh.new()
    nu, nv = 24, 9
    rows = [[bm.verts.new(on_dome(ang_dir(360 * a / nu, 8 + 100 * b / (nv - 1)))) for a in range(nu)]
            for b in range(nv)]
    for r0, r1 in zip(rows, rows[1:]):
        for a in range(nu):
            bm.faces.new((r0[a], r0[(a + 1) % nu], r1[(a + 1) % nu], r1[a]))
    me = bpy.data.meshes.new("GUIDE_hair_volume")
    bm.to_mesh(me)
    bm.free()
    dome = bpy.data.objects.new(me.name, me)
    a, L = Ref.att, LIMITS
    lo = Vector((-L["side"], a.y - L["front"], a.z - L["down"]))
    hi = Vector((L["side"], a.y + L["back"], a.z + L["up"]))
    bm = bmesh.new()
    for v in bmesh.ops.create_cube(bm, size=1.0)["verts"]:
        v.co = Vector((lo[k] + (v.co[k] + .5) * (hi[k] - lo[k]) for k in range(3)))
    me = bpy.data.meshes.new("GUIDE_limit_3x5x3.5_studs")
    bm.to_mesh(me)
    bm.free()
    box = bpy.data.objects.new(me.name, me)
    for ob in (dome, box):
        coll.objects.link(ob)
        ob.display_type = "WIRE"
        ob.hide_render = True
        ob.hide_select = True


# ── stage 2: curves ──────────────────────────────────────────────────────────────────────
def poly_curve(name, pts, coll, cyclic, loc):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "NONE"
    sp = cu.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    for p, (x, y) in zip(sp.points, pts):
        p.co = (x, y, 0.0, 1.0)
    sp.use_cyclic_u = cyclic
    ob = bpy.data.objects.new(name, cu)
    coll.objects.link(ob)
    ob.location = loc
    return ob


def eval_mesh(ob):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    return bpy.data.meshes.new_from_object(ob.evaluated_get(dg))


def mesh_coords(me):
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    return co.reshape(-1, 3)


def desired_normals(pts):
    P = [np.array(p[0]) for p in pts]
    out = []
    for k, (_, kind, hint) in enumerate(pts):
        T = P[min(k + 1, len(P) - 1)] - P[max(k - 1, 0)]
        T /= np.linalg.norm(T)
        h = np.array(hint)
        N = h - T * np.dot(h, T) if kind == "n" else np.cross(T, h)
        out.append(N / np.linalg.norm(N))
    return out


def ring_frames(ob, spec, prof):
    """Per control point: (tangent, direction the profile's +y actually points) from the bevel."""
    me = eval_mesh(ob)
    rings = mesh_coords(me).reshape(-1, prof.n, 3)
    bpy.data.meshes.remove(me)
    out = []
    for k in range(len(spec.pts)):
        r = rings[k * spec.res]
        X = r[prof.iR] - r[prof.iL]
        X /= np.linalg.norm(X)
        Y = r[prof.top].mean(axis=0) - 0.5 * (r[prof.iR] + r[prof.iL])
        Y -= X * np.dot(Y, X)
        Y /= np.linalg.norm(Y)
        out.append((np.cross(Y, X), Y))
    return out


def make_strand_curve(spec, coll, tools):
    """Bezier strand; tilt is solved so the profile's outer side follows the head / hang direction."""
    prof = PROFILES[spec.profile]
    cu = bpy.data.curves.new("HC_" + spec.name, "CURVE")
    cu.dimensions = "3D"
    cu.twist_mode = "MINIMUM"
    cu.resolution_u = spec.res
    cu.bevel_mode = "OBJECT"
    cu.bevel_object = tools["Profile_" + spec.profile]
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(spec.pts) - 1)
    for bp, p in zip(sp.bezier_points, spec.pts):
        bp.co = p[0]
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        bp.radius = 1.0
    sp.resolution_u = spec.res
    ob = bpy.data.objects.new(cu.name, cu)
    coll.objects.link(ob)

    want = desired_normals(spec.pts)

    def errors():
        res = []
        for (T, Y), N in zip(ring_frames(ob, spec, prof), want):
            N = N - T * np.dot(N, T)
            N /= np.linalg.norm(N)
            res.append(math.atan2(np.dot(np.cross(Y, N), T), np.dot(Y, N)))
        return res

    prev = 0.0
    for bp, a in zip(sp.bezier_points, errors()):
        a += round((prev - a) / (2 * math.pi)) * 2 * math.pi       # keep tilt continuous
        bp.tilt = prev = a
    worst = max(abs(a) for a in errors())
    if worst > math.radians(2.0):
        raise RuntimeError(f"{spec.name}: tilt solve off by {math.degrees(worst):.1f} deg")

    cu.taper_object = tools["Taper_" + spec.taper]
    cu.taper_radius_mode = "MULTIPLY"
    cu.use_fill_caps = True
    for bp in sp.bezier_points:
        bp.radius = spec.width
    ob["hair_group"], ob["uv_slot"], ob["striped"] = spec.group, spec.slot, spec.striped
    ob["crown"], ob["lit_under"] = spec.crown, spec.lit_under
    return ob


# ── stage 4 layout: texture regions in pixels (x0, x1, y0, y1), origin top-left ──────────
SLOTS = [(6 + i * 122, 120 + i * 122, 6, 1018) for i in range(5)] + [(902, 1018, 6, 880)]
UNDER = (618, 676, 6, 1018)
STRIPE = (686, 892, 6, 1018)
SCALP = (902, 1018, 896, 1018)
UV_MARGIN = 3


def rect_uv(rect, a, t):
    x0, x1, y0, y1 = rect
    x = x0 + UV_MARGIN + a * (x1 - x0 - 2 * UV_MARGIN)
    y = y0 + UV_MARGIN + t * (y1 - y0 - 2 * UV_MARGIN)
    return (x / TEX_SIZE, 1.0 - y / TEX_SIZE)


# ── stage 3 + 4: curve > mesh, clean up, UVs ─────────────────────────────────────────────
def split_cap(bm, face, ring0, prof):
    """Fill Caps leaves an n-gon: split it across the profile's widest line into quads."""
    if len(face.verts) < 5:
        return
    near = lambda p: min(face.verts, key=lambda v: (v.co - Vector(p)).length)   # noqa: E731
    bmesh.utils.face_split(face, near(ring0[prof.iL]), near(ring0[prof.iR]))


def strand_to_mesh(spec, ob):
    prof = PROFILES[spec.profile]
    n = prof.n
    R = (len(spec.pts) - 1) * spec.res + 1
    me = eval_mesh(ob)
    me.transform(ob.matrix_world)
    off = len(me.vertices) - n * R                  # Fill Caps prepends a copy of the root ring
    if off not in (0, n):
        raise RuntimeError(f"{spec.name}: unexpected curve mesh layout ({len(me.vertices)} verts)")
    rings = mesh_coords(me)[off:].reshape(R, n, 3)
    cent = rings.mean(axis=1)
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(cent, axis=0), axis=1))])
    s /= s[-1]
    tt = 0.44 + 0.56 * s                            # root hidden under upper layers: skip the gloss ring
    if spec.crown:                                  # pin the gloss ring to one latitude of the head
        v = cent - np.array(dome_c())
        u = np.degrees(np.arccos(np.clip(v[:, 2] / np.linalg.norm(v, axis=1), -1, 1)))
        hit = (u >= U_HALO) & (np.arange(R) > 0)
        if hit.any():
            k = int(np.argmax(hit))
            s_h = s[k - 1] + (s[k] - s[k - 1]) * (U_HALO - u[k - 1]) / max(u[k] - u[k - 1], 1e-6)
            tt = np.interp(s, [0.0, float(np.clip(s_h, .02, .98)), 1.0], [0.0, T_HALO, 1.0])
    top_rect = STRIPE if spec.striped else SLOTS[spec.slot]

    bm = bmesh.new()
    bm.from_mesh(me)
    bpy.data.meshes.remove(me)
    uvl = bm.loops.layers.uv.verify()
    part = bm.faces.layers.int.new("part")          # 0 outer, 1 underside, 2 root cap, 3 scalp
    for f in bm.faces:
        idx = [v.index for v in f.verts]
        if max(idx) < off:
            f[part] = 2
            for lp in f.loops:
                x, y = prof.pts[lp.vert.index]
                lp[uvl].uv = rect_uv(UNDER, 0.5 - x, 0.01 + 0.05 * (y + 0.1))
            continue
        jk = [divmod(i - off, n) for i in idx]
        ks = sorted({k for _, k in jk})
        e = n - 1 if (ks[0] == 0 and ks[-1] == n - 1) else ks[0]
        region, a0, a1 = prof.segs[e]
        f[part] = 0 if region == "top" else 1
        lit = region == "top" or spec.lit_under          # thin flyaways: no dark underside
        for lp, (jr, k) in zip(f.loops, jk):
            a = 0.5 * (a0 + a1) if jr == R - 1 else (a0 if k == e else a1)
            lp[uvl].uv = rect_uv(top_rect if lit else UNDER, a, tt[jr] if lit else s[jr])

    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)        # weld cap + collapse the tip
    caps = [f for f in bm.faces if f[part] == 2]
    if len(caps) > 1:
        caps = bmesh.ops.dissolve_faces(bm, faces=caps)["region"]
    for f in caps:
        f[part] = 2
        split_cap(bm, f, rings[0], prof)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    if any(not e.is_manifold for e in bm.edges):
        raise RuntimeError(f"{spec.name}: strand is not a closed volume")
    out = bpy.data.meshes.new("tmp_" + spec.name)
    bm.to_mesh(out)
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    bm.free()
    return out, tris


SCALP_PROFILE = [(0.0, .655), (.26, .652), (.45, .628), (.548, .566), (.615, .46), (.652, .33),
                 (.655, .10), (.655, -.15), (.652, -.38), (.62, -.52), (.57, -.61)]   # (radius, z)
SCALP_RINGS = (.145, .27, .36, .47, .62, .80, 1.0)


def scalp_mesh(nseg=16):
    """Dark closed cap hugging the head so no skin shows between strands (open over the face)."""
    pr = np.array(SCALP_PROFILE)
    arc = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pr, axis=0), axis=1))])
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new("UVMap")           # same name as the converted curves' layer
    part = bm.faces.layers.int.new("part")
    hc = Ref.head_c
    cols = []
    for c in range(nseg):
        phi = 360.0 * c / nseg
        a = min(phi, 360 - phi)
        zcut = np.interp(a, [0, 30, 45, 67.5, 90, 180], [.47, .47, .40, -.20, -.61, -.61])
        s_cut = np.interp(-zcut, -pr[:, 1], arc)
        col = []
        for fr in SCALP_RINGS:
            r, z = np.interp(fr * s_cut, arc, pr[:, 0]), np.interp(fr * s_cut, arc, pr[:, 1])
            p = math.radians(phi)
            col.append(bm.verts.new(hc + Vector((r * math.sin(p), -r * math.cos(p), z))))
        cols.append(col)
    top = bm.verts.new(hc + Vector((0, 0, pr[0, 1])))
    inner = bm.verts.new(hc + Vector((0, 0, .15)))                 # closes the cap inside the head
    rows = len(SCALP_RINGS)

    def face(verts, grid):                                         # grid: (column, row) per corner
        f = bm.faces.new(verts)
        f[part] = 3
        for lp, (col, row) in zip(f.loops, grid):
            lp[uvl].uv = rect_uv(SCALP, col / nseg, row / (rows + 1))

    for c in range(nseg):
        a, b = cols[c], cols[(c + 1) % nseg]
        face((top, a[0], b[0]), ((c + .5, 0), (c, 1), (c + 1, 1)))
        for k in range(rows - 1):
            face((a[k], a[k + 1], b[k + 1], b[k]), ((c, k + 1), (c, k + 2), (c + 1, k + 2), (c + 1, k + 1)))
        face((inner, b[-1], a[-1]), ((c + .5, rows + 1), (c + 1, rows), (c, rows)))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    out = bpy.data.meshes.new("tmp_scalp")
    bm.to_mesh(out)
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    bm.free()
    return out, tris


# ── stage 5: texture (painted directly in UV space, top-left origin, sRGB 0..1) ──────────
def _hex(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255.0


def _mix(a, b, t):
    return a + (b - a) * (t[..., None] if isinstance(t, np.ndarray) else t)


def paint_strand(img, hgt, rect, seed, halo=.8, dark=0.0, tip_light=.28, tip_band=0.0,
                 light_ratio=.38, stripes=None, flat=None):
    C_MAIN, C_ROOT, C_HI = _hex(COL_MAIN), _hex(COL_ROOT), _hex(COL_HI)
    C_GLOSS = C_HI + (1 - C_HI) * .32
    rng = np.random.default_rng(seed)
    x0, x1, y0, y1 = rect
    pad = 4
    xs = np.arange(max(x0 - pad, 0), min(x1 + pad, TEX_SIZE))
    ys = np.arange(max(y0 - pad, 0), min(y1 + pad, TEX_SIZE))
    A, T = np.meshgrid(np.clip((xs + .5 - x0) / (x1 - x0), 0, 1),
                       np.clip((ys + .5 - y0) / (y1 - y0), 0, 1))
    A, T = A.astype(np.float32), T.astype(np.float32)
    if flat is not None:
        img[np.ix_(ys, xs)] = flat * (1 - .12 * T[..., None])
        return
    main = _mix(C_MAIN, C_ROOT, dark)
    stops_t = [0, .10, .24, .60, 1.0]
    stops_c = [C_ROOT, _mix(C_ROOT, main, .45), main, main, _mix(main, C_HI, tip_light)]
    col = np.stack([np.interp(T, stops_t, [c[i] for c in stops_c]) for i in range(3)], axis=-1)

    dk = np.zeros(A.shape, np.float32)                             # dark / light painted fibres
    lt = np.zeros(A.shape, np.float32)
    w = x1 - x0
    for i in range(int(64 * w / 114)):
        strong, broad = i < 3, 3 <= i < 10                         # clump splits / soft tonal bands
        xf = (.25 + .25 * i + rng.uniform(-.06, .06)) if strong else rng.uniform(.02, .98)
        sg = (rng.uniform(1.4, 2.0) if strong else rng.uniform(5, 12) if broad else rng.uniform(.55, 1.5)) / w
        amp = rng.uniform(.5, .62) if strong else rng.uniform(.14, .26) if broad else rng.uniform(.2, .6)
        t0, ln = (-.2, 1.5) if strong else (rng.uniform(-.3, .7), rng.uniform(.35, 1.3))
        wob = rng.uniform(0, .012) * np.sin(2 * np.pi * (rng.uniform(.4, 1.8) * T + rng.random()))
        env = np.clip((T - t0) / .1, 0, 1) * np.clip((t0 + ln - T) / .1, 0, 1)
        g = np.exp(-.5 * ((A - xf - wob) / sg) ** 2) * env * amp
        if strong or rng.random() > light_ratio:
            dk = np.maximum(dk, g)
        else:
            lt = np.maximum(lt, g)
    col = _mix(col, C_ROOT, dk * .85)
    col = _mix(col, C_HI, lt * .8)

    band = np.zeros(A.shape, np.float32)
    for centre, amount in ((T_HALO, halo), (.86, tip_band)):       # gloss ring (+ lit flipped tips)
        if amount <= 0:
            continue
        ncol = 9                                                   # zig-zag of streaks over a soft glow
        edges = np.concatenate([[0.0], np.sort(rng.uniform(.04, .96, ncol - 1)), [1.0]])
        ci = np.clip(np.searchsorted(edges, A, side="right") - 1, 0, ncol - 1)
        mid, half = (edges[:-1] + edges[1:]) / 2, np.maximum((edges[1:] - edges[:-1]) / 2, 1e-3)
        top = centre + rng.uniform(-.045, .02, ncol)
        length = rng.uniform(.09, .20, ncol)
        power = rng.uniform(.6, 1.0, ncol)
        across = np.clip(1 - ((A - mid[ci]) / half[ci]) ** 2, 0, 1) ** .6
        d = (T - top[ci]) / length[ci]
        b = np.clip(d / .08, 0, 1) * np.clip(1 - d, 0, 1) ** 1.3 * across * power[ci]
        b = np.maximum(b, .45 * np.exp(-.5 * ((T - centre - .04) / .06) ** 2))
        band = np.maximum(band, b * amount * (1 - .4 * np.abs(2 * A - 1) ** 1.6) * (1 - .7 * dk))
    col = _mix(col, C_HI, band)
    col = _mix(col, C_GLOSS, .55 * band ** 2)

    kx = np.linspace(0, 1, 9)
    if stripes:                                                    # black tiger stripes
        C_BLACK = _hex("#14070A")
        for k, tc in enumerate(stripes):
            half = .043 * (1 + .30 * (2 * A - 1) * (1 if k % 2 else -1))
            jit = np.interp(A, kx, rng.uniform(-.009, .009, 9)) + .014 * (lt - dk)
            m = np.clip((half - np.abs(T - tc - jit)) / .006, 0, 1)
            m = m * m * (3 - 2 * m)
            col = _mix(col, _mix(C_BLACK, C_BLACK * 2.6, lt), m)
    col = _mix(col, C_ROOT, .55 * np.abs(2 * A - 1) ** 2.6)         # shaded strand edges
    img[np.ix_(ys, xs)] = col
    hgt[np.ix_(ys, xs)] = .7 * (1 - (2 * A - 1) ** 2) + .35 * (lt - dk)


def paint_textures():
    C_MAIN, C_ROOT = _hex(COL_MAIN), _hex(COL_ROOT)
    img = np.empty((TEX_SIZE, TEX_SIZE, 3), np.float32)
    img[:] = _mix(C_ROOT, C_MAIN, .4)
    hgt = np.zeros((TEX_SIZE, TEX_SIZE), np.float32)
    variants = [dict(halo=.85), dict(halo=.65, dark=.06, tip_light=.30),
                dict(halo=1.0, tip_light=.40, light_ratio=.5), dict(halo=.25, dark=.30, tip_light=.12),
                dict(halo=.5, dark=.04, tip_light=.30, tip_band=.5), dict(halo=0.0, dark=.18, tip_light=.34)]
    for i, (rect, kw) in enumerate(zip(SLOTS, variants)):
        paint_strand(img, hgt, rect, SEED * 100 + i, **kw)
    paint_strand(img, hgt, STRIPE, SEED * 100 + 50, halo=.7, stripes=(.25, .45, .64, .82))
    under = np.zeros_like(img)
    paint_strand(under, hgt.copy(), UNDER, SEED * 100 + 60, halo=0.0, dark=.32, tip_light=0.0)
    x0, x1, y0, y1 = UNDER
    img[y0 - 4:y1 + 4, x0 - 4:x1 + 4] = under[y0 - 4:y1 + 4, x0 - 4:x1 + 4]
    hgt[y0 - 4:y1 + 4, x0 - 4:x1 + 4] = 0.0
    paint_strand(img, hgt, SCALP, 0, flat=C_ROOT * .9)

    k = TEX_SIZE // NORMAL_SIZE                                    # tangent-space normal, OpenGL (Y+)
    h = hgt.reshape(NORMAL_SIZE, k, NORMAL_SIZE, k).mean(axis=(1, 3))
    gy, gx = np.gradient(h)
    nx, ny, nz = -gx * 4.0, gy * 4.0, np.ones_like(h)
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    nrm = np.stack([nx / ln, ny / ln, nz / ln], axis=-1) * .5 + .5
    return np.clip(img, 0, 1), nrm.astype(np.float32)


def save_image(name, arr, path, colorspace="sRGB"):
    h, w, _ = arr.shape
    im = bpy.data.images.new(name, w, h, alpha=False)
    im.colorspace_settings.name = colorspace
    rgba = np.ones((h, w, 4), np.float32)
    rgba[..., :3] = arr[::-1]
    im.pixels.foreach_set(rgba.ravel())
    im.filepath_raw = path
    im.file_format = "PNG"
    im.save()
    return im


# ── stage 6: checks ──────────────────────────────────────────────────────────────────────
def run_checks(ob):
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    uvl = bm.loops.layers.uv.active
    co = np.array([ob.matrix_world @ v.co for v in bm.verts])
    a, L = Ref.att, LIMITS
    ext = dict(side=np.abs(co[:, 0] - a.x).max(), up=co[:, 2].max() - a.z, down=a.z - co[:, 2].min(),
               front=a.y - co[:, 1].min(), back=co[:, 1].max() - a.y)
    shells, inverted = 0, 0
    for comp in _components(bm):
        shells += 1
        vol = 0.0
        for f in {f for v in comp for f in v.link_faces}:
            vs = [v.co for v in f.verts]
            vol += sum(vs[0].dot(vs[i].cross(vs[i + 1])) for i in range(1, len(vs) - 1))
        inverted += vol <= 0
    mirrored = 0
    for f in bm.faces:
        p = [lp.vert.co for lp in f.loops][:3]
        q = [lp[uvl].uv for lp in f.loops][:3]
        geo = (p[1] - p[0]).cross(p[2] - p[0]).dot(f.normal)
        uv = (q[1] - q[0]).cross(q[2] - q[0])
        mirrored += geo * uv < 0
    uvs = np.array([lp[uvl].uv[:] for f in bm.faces for lp in f.loops])
    r = dict(
        triangles=sum(len(f.verts) - 2 for f in bm.faces),
        quads=sum(len(f.verts) == 4 for f in bm.faces),
        tris=sum(len(f.verts) == 3 for f in bm.faces),
        ngons=sum(len(f.verts) > 4 for f in bm.faces),
        verts=len(bm.verts),
        non_manifold_edges=sum(len(e.link_faces) != 2 for e in bm.edges),
        loose_verts=sum(not v.link_faces for v in bm.verts),
        zero_area_faces=sum(f.calc_area() < 1e-9 for f in bm.faces),
        duplicate_verts=len(bmesh.ops.find_doubles(bm, verts=bm.verts, dist=1e-5)["targetmap"]),
        closed_shells=shells, inverted_shells=int(inverted), mirrored_uv_faces=int(mirrored),
        uv_min=float(uvs.min()), uv_max=float(uvs.max()),
        uv_maps=len(me.uv_layers), materials=len(me.materials),
        color_attributes=len(me.color_attributes),
        transforms_applied=bool(ob.matrix_world.is_identity),
        size_xyz=[round(float(v), 3) for v in co.max(axis=0) - co.min(axis=0)],
        extent_from_attachment={k: round(float(v), 3) for k, v in ext.items()},
        bbox_centre_from_attachment=[round(float(v), 3) for v in
                                     (co.max(axis=0) + co.min(axis=0)) / 2 - np.array(a)],
    )
    bm.free()
    fails = []
    if r["triangles"] > TRI_LIMIT:
        fails.append("triangle budget")
    for key in ("ngons", "non_manifold_edges", "loose_verts", "zero_area_faces", "duplicate_verts",
                "inverted_shells", "color_attributes"):
        if r[key]:
            fails.append(key)
    if r["uv_min"] < 0 or r["uv_max"] > 1 or r["uv_maps"] != 1 or r["materials"] != 1:
        fails.append("uv/material")
    if not r["transforms_applied"]:
        fails.append("transforms")
    fails += [f"size limit ({k})" for k, v in ext.items() if v > L[k]]
    return r, fails


def specs_from_curves():
    """Strand specs read back from the curve objects in the file (used after hand edits)."""
    pairs = []
    for ob in bpy.data.objects:
        if ob.type != "CURVE" or "hair_group" not in ob:
            continue
        cu, sp = ob.data, ob.data.splines[0]
        spec = Spec(ob.name[3:], ob["hair_group"], [None] * len(sp.bezier_points), 0.0,
                    cu.taper_object.name[6:], cu.bevel_object.name[8:], sp.resolution_u, ob["uv_slot"],
                    bool(ob["crown"]), bool(ob["striped"]), bool(ob["lit_under"]))
        pairs.append((spec, ob))
    return pairs


# ── pipeline ─────────────────────────────────────────────────────────────────────────────
SOURCE_COLLECTIONS = ("HAIR_00_Reference_R15", "HAIR_01_Blocking", "HAIR_02_Curve_Tools", "HAIR_03_Curves")


def main():
    os.makedirs(OUT, exist_ok=True)
    keep = "--keep-curves" in ARGS                  # re-export hand-edited curves, don't regenerate
    if keep and "HAIR_03_Curves" not in bpy.data.collections:
        bpy.ops.wm.open_mainfile(filepath=os.path.join(OUT, NAME + ".blend"))
    elif not keep and bpy.app.background:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    for c in [c for c in bpy.data.collections if c.name.startswith("HAIR_")]:
        if keep and (c.name in SOURCE_COLLECTIONS or c.name.startswith("HAIR_03_")):
            continue                                # everything else is a previous build: drop it
        for o in list(c.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(c)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for d in [d for d in block if d.name.startswith(NAME)]:
            block.remove(d)
    scene = bpy.context.scene
    layers = bpy.context.view_layer.layer_collection.children

    if keep:
        print("[0-2] keeping the reference and the curves in this file")
        Ref.head_c, Ref.att = Vector(scene["hair_head_c"]), Vector(scene["hair_att"])
        for name in SOURCE_COLLECTIONS[1:]:
            layers[name].hide_viewport = False      # hidden objects are not evaluated
        pairs = specs_from_curves()
    else:
        print("[0] reference avatar")
        load_reference(new_collection(SOURCE_COLLECTIONS[0]))
        scene["hair_head_c"], scene["hair_att"] = list(Ref.head_c), list(Ref.att)
        print(f"    head centre z={Ref.head_c.z:.3f}  HairAttachment z={Ref.att.z:.3f}")

        print("[1] blocking")
        build_blocking(new_collection(SOURCE_COLLECTIONS[1], hide_render=True))

        print("[2] curves")
        c_tools = new_collection(SOURCE_COLLECTIONS[2], hide_render=True)
        tools = {}
        for i, (k, p) in enumerate(PROFILES.items()):
            tools["Profile_" + k] = poly_curve("Profile_" + k, p.pts, c_tools, True, (3.0, 0, 3 - i * .6))
        for i, (k, pts) in enumerate(TAPERS.items()):
            tools["Taper_" + k] = poly_curve("Taper_" + k, pts, c_tools, False, (4.2, 0, 3 - i * .6))
        c_curves = new_collection(SOURCE_COLLECTIONS[3], hide_render=True)
        groups, pairs = {}, []
        for spec in build_specs():
            if spec.group not in groups:
                groups[spec.group] = new_collection(f"HAIR_03_{len(groups) + 1}_{spec.group}", c_curves, True)
            pairs.append((spec, make_strand_curve(spec, groups[spec.group], tools)))
    print(f"    {len(pairs)} strands")

    print("[3] convert to mesh + clean, [4] UVs")
    master = bmesh.new()
    budget = {}
    for spec, ob in pairs:
        me, tris = strand_to_mesh(spec, ob)
        master.from_mesh(me)
        bpy.data.meshes.remove(me)
        budget[spec.group] = budget.get(spec.group, 0) + tris
    me, budget["Scalp"] = scalp_mesh()
    master.from_mesh(me)
    bpy.data.meshes.remove(me)
    part = master.faces.layers.int["part"]
    sharp = math.radians(62)
    for f in master.faces:
        f.smooth = True
    for e in master.edges:                          # crisp strand edges, smooth everywhere else
        f0, f1 = e.link_faces
        e.smooth = f0[part] == f1[part] and e.calc_face_angle() < sharp
    master.faces.layers.int.remove(part)
    hair_me = bpy.data.meshes.new(NAME)
    master.to_mesh(hair_me)
    master.free()
    if hasattr(hair_me, "use_auto_smooth"):         # Blender 4.0
        hair_me.use_auto_smooth = True
        hair_me.auto_smooth_angle = math.pi
    hair_me.uv_layers[0].name = "UVMap"
    hair = bpy.data.objects.new(NAME, hair_me)
    new_collection("HAIR_04_EXPORT").objects.link(hair)
    for k, v in budget.items():
        print(f"    {k:8s} {v:5d} tris")

    print("[5] texture")
    color, normal = paint_textures()
    tex = save_image(NAME + "_Color", color, os.path.join(OUT, NAME + "_Color.png"))
    save_image(NAME + "_Normal", normal, os.path.join(OUT, NAME + "_Normal.png"), "Non-Color")
    mat = bpy.data.materials.new(NAME + "_MAT")
    mat.use_nodes = True
    nt = mat.node_tree
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = tex
    node.location = (-320, 300)
    bsdf = nt.nodes["Principled BSDF"]
    nt.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.45
    hair_me.materials.append(mat)

    print("[6] checks")
    report, fails = run_checks(hair)
    lines = [f"{k}: {v}" for k, v in report.items()]
    lines.append("RESULT: " + ("PASS" if not fails else "FAIL - " + ", ".join(fails)))
    print("    " + "\n    ".join(lines))
    with open(os.path.join(OUT, NAME + "_report.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("[7] export")
    for o in scene.objects:
        o.select_set(False)
    hair.select_set(True)
    bpy.context.view_layer.objects.active = hair
    bpy.ops.export_scene.fbx(                       # Roblox's documented settings for rigid accessories
        filepath=os.path.join(OUT, NAME + ".fbx"), use_selection=True, object_types={"MESH"},
        apply_scale_options="FBX_SCALE_UNITS", global_scale=1.0, path_mode="COPY",
        embed_textures=True, bake_anim=False, add_leaf_bones=False, mesh_smooth_type="OFF")
    for name in SOURCE_COLLECTIONS[1:]:
        layers[name].hide_viewport = True           # editable source stays in the file, hidden
    for im in bpy.data.images:
        if im.filepath_raw and not im.packed_file:
            im.pack()
            im.filepath_raw = "//" + os.path.basename(im.filepath_raw)     # portable: next to the .blend
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, NAME + ".blend"), compress=True)
    print("done ->", OUT)
    if fails:
        raise SystemExit("checks failed: " + ", ".join(fails))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Génère le logo du module — ``static/description/icon.png``.

Le logo est *dessiné*, pas peint : il est donc reproductible et modifiable.
Pour le régénérer après avoir changé une couleur ou une proportion :

    python3 tools/make_icon.py

Parti pris graphique
--------------------
Le module est le fourre-tout du secrétariat ; son symbole doit rester
« paperasse » plutôt que « carburant », pour ne pas devenir faux le jour où
une deuxième fonctionnalité arrivera. D'où un **bon détachable** — corps
blanc, souche perforée à gauche — qui vaut pour tout registre tenu par le
secrétariat. La **goutte ambre** en pastille dit la fonctionnalité du jour :
c'est l'accent, pas le sujet.

La tuile arrondie, le dégradé et le halo reprennent la facture des autres
modules ICP (hr_management, equipment_management…). Les couleurs sont celles
du tableau de bord (``static/src/css/secretariat_dashboard.css``).

Tout est dessiné à quatre fois la taille finale puis réduit en Lanczos :
c'est ce qui donne des bords lisses sans anticrénelage manuel.
"""

import os

from PIL import Image, ImageDraw, ImageFilter

# --- Sortie ---------------------------------------------------------------
SIZE = 1024
SCALE = 4
S = SIZE * SCALE

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, '..', 'static', 'description', 'icon.png')

# --- Palette (cf. secretariat_dashboard.css) ------------------------------
TILE_TOP = (74, 117, 192)       # #4a75c0
TILE_BOTTOM = (22, 41, 79)      # #16294f
GLOW = (48, 84, 150)            # #305496 — --sec-primary
ACCENT = (245, 158, 11)         # #f59e0b — --sec-accent
ACCENT_DARK = (217, 119, 6)
PAPER = (255, 255, 255)
INK = (48, 84, 150)
INK_SOFT = (169, 185, 214)

# --- Géométrie (en fractions du côté) -------------------------------------
# Les proportions sont calculées pour que le bon, une fois incliné, tienne
# dans la tuile : demi-largeur·cos8 + demi-hauteur·sin8 = 0,27 < 0,355.
TILE_MARGIN = 0.145
TILE_RADIUS = 0.175
TICKET_W, TICKET_H = 0.500, 0.335
TICKET_RADIUS = 0.034
TICKET_ANGLE = -8              # degrés, sens antihoraire
STUB_RATIO = 0.30              # part du bon occupée par la souche
DROP_CX, DROP_CY, DROP_R = 0.690, 0.690, 0.082


def px(fraction):
    """Fraction du côté -> pixels de la toile de travail."""
    return int(round(fraction * S))


# =========================================================================
# BRIQUES
# =========================================================================

def diagonal_gradient(size, top, bottom):
    """Dégradé en diagonale (haut-gauche clair, bas-droite sombre).

    Calculé sur une vignette puis agrandi : c'est l'interpolation qui fait le
    lissage. Une rotation d'un dégradé vertical laisserait des coins noirs
    dans la tuile.
    """
    small = 64
    gradient = Image.new('RGB', (small, small))
    pixels = gradient.load()
    for y in range(small):
        for x in range(small):
            ratio = min(1.0, (x / (small - 1)) * 0.35 + (y / (small - 1)) * 0.65)
            pixels[x, y] = tuple(
                int(round(top[i] + (bottom[i] - top[i]) * ratio)) for i in range(3))
    return gradient.resize((size, size), Image.BICUBIC)


def tile_mask():
    """Masque de la tuile arrondie."""
    mask = Image.new('L', (S, S), 0)
    draw = ImageDraw.Draw(mask)
    margin = px(TILE_MARGIN)
    draw.rounded_rectangle(
        [margin, margin, S - margin, S - margin],
        radius=px(TILE_RADIUS), fill=255)
    return mask


def glow_layer(mask):
    """Halo diffus derrière la tuile, comme sur les autres icônes ICP."""
    halo = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    halo.paste(GLOW + (210,), (0, 0), mask)
    return halo.filter(ImageFilter.GaussianBlur(px(0.055)))


def ticket_layer():
    """Le bon détachable : souche perforée à gauche, lignes à droite."""
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    width, height = px(TICKET_W), px(TICKET_H)
    left = (S - width) // 2
    top = (S - height) // 2 - px(0.022)
    right, bottom = left + width, top + height
    radius = px(TICKET_RADIUS)

    draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=PAPER)

    # Souche : perforation verticale en pointillé.
    stub_x = left + int(width * STUB_RATIO)
    dash, gap = px(0.019), px(0.014)
    thickness = px(0.0075)
    y = top + px(0.030)
    while y < bottom - px(0.030):
        draw.rounded_rectangle(
            [stub_x - thickness // 2, y, stub_x + thickness // 2, min(y + dash, bottom - px(0.030))],
            radius=thickness // 2, fill=INK_SOFT)
        y += dash + gap

    # Encoches semi-circulaires en haut et en bas de la perforation : c'est
    # ce détail qui fait lire « bon à détacher » plutôt que « feuille ».
    notch = px(0.026)
    for cy in (top, bottom):
        draw.ellipse([stub_x - notch, cy - notch, stub_x + notch, cy + notch],
                     fill=(0, 0, 0, 0))

    # Souche : un gros numéro stylisé (deux barres épaisses).
    bar_x0 = left + px(0.030)
    bar_x1 = stub_x - px(0.032)
    draw.rounded_rectangle(
        [bar_x0, top + px(0.085), bar_x1, top + px(0.085) + px(0.028)],
        radius=px(0.014), fill=INK)
    draw.rounded_rectangle(
        [bar_x0, top + px(0.140), bar_x0 + int((bar_x1 - bar_x0) * 0.62),
         top + px(0.140) + px(0.028)],
        radius=px(0.014), fill=INK_SOFT)

    # Corps : trois lignes d'écriture, la dernière plus courte.
    line_x0 = stub_x + px(0.038)
    line_x1 = right - px(0.034)
    for index, ratio in enumerate((1.0, 1.0, 0.55)):
        y0 = top + px(0.085) + index * px(0.078)
        draw.rounded_rectangle(
            [line_x0, y0, line_x0 + int((line_x1 - line_x0) * ratio), y0 + px(0.030)],
            radius=px(0.015), fill=INK if index == 0 else INK_SOFT)

    return layer.rotate(TICKET_ANGLE, resample=Image.BICUBIC, expand=False)


# Hauteur de la pointe de la goutte, en rayons.
DROP_APEX = 2.15


def drop_layer():
    """Goutte de carburant, cerclée de la couleur de la tuile pour se détacher."""
    layer = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy, r = px(DROP_CX), px(DROP_CY), px(DROP_R)

    def teardrop(radius, fill, apex=None):
        """Cercle du bas + triangle EXACTEMENT tangent.

        Les points de tangence se calculent (ils ne s'approximent pas) : avec
        une pointe à la distance d du centre, ils valent
        (cx ± r·√(1−(r/d)²), cy − r²/d). Une valeur approchée laisse une
        couture visible à la jonction des deux formes.

        ``apex`` est réglable pour que le liseré garde une épaisseur
        constante : le faire grandir proportionnellement épaissirait le trait
        à la pointe, où il se verrait comme une pique.
        """
        apex = apex if apex is not None else radius * DROP_APEX
        ratio = radius / apex
        touch_x = radius * (1 - ratio ** 2) ** 0.5
        touch_y = radius * ratio
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=fill)
        draw.polygon([
            (cx, cy - apex),
            (cx - touch_x, cy - touch_y),
            (cx + touch_x, cy - touch_y),
        ], fill=fill)

    ring = px(0.014)
    teardrop(r + ring, TILE_BOTTOM, apex=r * DROP_APEX + ring)
    teardrop(r, ACCENT_DARK)
    teardrop(r * 0.93, ACCENT, apex=r * DROP_APEX - px(0.004))

    # Reflet : tache claire diffuse en haut à gauche, comme sur un liquide.
    # Floutée à part, sans quoi elle se lirait comme un trou dans la goutte.
    highlight = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).ellipse(
        [cx - int(r * 0.62), cy - int(r * 0.70),
         cx - int(r * 0.16), cy - int(r * 0.24)], fill=(255, 255, 255, 125))
    highlight = highlight.filter(ImageFilter.GaussianBlur(px(0.006)))
    return Image.alpha_composite(layer, highlight)


def shadow_of(layer, offset, blur, opacity):
    """Ombre portée d'un calque, décalée puis floutée à partir de son alpha."""
    shadow = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    shadow.paste((10, 22, 45, opacity),
                 (px(offset[0]), px(offset[1])), layer.split()[3])
    return shadow.filter(ImageFilter.GaussianBlur(px(blur)))


# =========================================================================
# ASSEMBLAGE
# =========================================================================

def build():
    mask = tile_mask()

    # Le fond et les symboles sont assemblés sur toute la toile, PUIS découpés
    # d'un seul coup par le masque de la tuile. C'est ce qui garantit la
    # silhouette : rien ne peut déborder, quelles que soient les proportions.
    content = Image.new('RGBA', (S, S), (0, 0, 0, 255))
    content.paste(diagonal_gradient(S, TILE_TOP, TILE_BOTTOM), (0, 0))

    ticket = ticket_layer()
    content = Image.alpha_composite(
        content, shadow_of(ticket, (0.006, 0.011), 0.018, 120))
    content = Image.alpha_composite(content, ticket)

    drop = drop_layer()
    content = Image.alpha_composite(
        content, shadow_of(drop, (0.003, 0.007), 0.012, 100))
    content = Image.alpha_composite(content, drop)

    tile = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    tile.paste(content, (0, 0), mask)

    canvas = Image.alpha_composite(glow_layer(mask), tile)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == '__main__':
    icon = build()
    icon.save(os.path.normpath(TARGET), 'PNG', optimize=True)
    print("Écrit : %s (%s×%s)" % (os.path.normpath(TARGET), SIZE, SIZE))

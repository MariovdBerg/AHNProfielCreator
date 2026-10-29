"""
Schematisatie van een dijkprofiel tot een Hydra-NL profiel: voorland, buitentalud met 0, 1 of 2 bermen, kruin.

Het representatieve profiel (afstand x tot de dijklijn, hoogte z; negatief = buiten) wordt in één keer gefit
met een stuksgewijs lineair profiel::

    voorland | talud | (berm | talud) x k | kruin

De hellingen (tan = dz/dx) zijn per deel begrensd met de regels van Hydra-NL/Ringtoets, zodat het resultaat
altijd een geldig profiel is. Die grenzen komen uit de controle van `prflcheck` (ProfielCheckPython, zie
`hydra_grenzen`); een wijziging daar werkt dus door in de fit. Bij gegeven knikpunten is de fit een
kleinste-kwadratenprobleem met grenzen; de knikpunten worden gezocht door een berm in te voegen op een raster en
ze daarna één voor één (en een berm als geheel) te verschuiven. Alle kandidaten worden tegelijk doorgerekend.

`prflcheck` wordt pas geladen als er gefit wordt: zonder dat pakket werkt de rest van `ahnprofielcreator` wel.
"""

import dataclasses

import numpy as np

PRFLCHECK_URL = "https://github.com/MariovdBerg/ProfielCheckPython/archive/refs/heads/main.zip"
# Marge op de grenzen van prflcheck, zodat een profiel na afronden niet net over een grens valt: een relatief deel
# van de grens, plus de grootste afwijking in de helling door afronden van z op `decimalen` over de kortste breedte.
MARGE_REL = 0.002


def laad_prflcheck():
    """Het pakket prflcheck (ProfielCheckPython), met een duidelijke melding als het ontbreekt."""
    try:
        import prflcheck
        import prflcheck.checks
    except ImportError:
        raise ImportError(
            "Voor het schematiseren is het pakket prflcheck (ProfielCheckPython) nodig. Installeer het in de "
            f"omgeving met: pip install {PRFLCHECK_URL}"
        ) from None
    return prflcheck


@dataclasses.dataclass
class FitInstellingen:
    """
    Instellingen van de fit.

    Bermcriteria: een extra berm maakt het profiel altijd iets beter passend, ook als er geen berm is. Daarom moet
    de RMSE (de gemiddelde verticale afwijking van het representatieve profiel, over het dijkdeel) door de berm
    zowel met minstens `berm_winst_rel` (fractie) als met minstens `berm_winst_abs` [m] dalen. Daarnaast is een berm
    minstens `min_bermbreedte` [m] breed en zijn de taluds ernaast elk minstens `min_taludhoogte` [m] hoog; anders is
    het voorland of een afgeronde kruin.
    """

    min_bermbreedte: float = 3.0  # [m]
    min_taludhoogte: float = 0.5  # [m]
    berm_winst_rel: float = 0.2  # [-]
    berm_winst_abs: float = 0.03  # [m]
    min_taludbreedte: float = 2.0  # [m] minimale breedte van een talud
    stap: float = 0.5  # [m] raster van de knikpunten en van het representatieve profiel
    # [m] in de fit telt alleen het voorland tot zover (plus de dijkbreedte) buiten de eerst globaal gevonden teen;
    # een verre, hogere uiterwaard trekt de teen dan niet omlaag. Het voorland in het .prfl gebruikt het hele profiel.
    voorland_fit: float = 30.0
    voorland_delen: int = 1  # Delen voorland in de fit (alleen om de teen te vinden)
    decimalen: int = 3  # Afronding van x en z in het .prfl


@dataclasses.dataclass
class HydraGrenzen:
    """Grenzen voor de fit, afgeleid uit de controle van prflcheck (zie `hydra_grenzen`)."""

    tan: dict  # soort deel -> (laagste, hoogste) tan = dz/dx, met marge
    max_bermen: int
    min_voorland: float  # [m] minimale afstand tussen voorlandpunten
    voorland_tan: float  # hoogste |tan| van het voorland, met marge


def hydra_grenzen(inst):
    """
    Grenzen voor de hellingen uit `prflcheck.checks`, met een marge voor afronden (zie `MARGE_REL`):

    - talud ('t'): van TANASTL (1:8) tot TANAMAX (1:1);
    - berm ('b'): van TANAMIN (1:100) tot TANAFLW (1:15);
    - kruin ('k'): van 0 tot TANAFLW (mag licht hellen: het laatste dijkpunt is de buitenkruinlijn);
    - voorland ('v'): tot TANAMINVOORLAND (1:10) omhoog en omlaag.
    """
    checks = laad_prflcheck().checks
    afronding = 10.0 ** -inst.decimalen  # Grootste afwijking in dz door afronden van beide punten

    def marge(tan, breedte):
        return MARGE_REL * tan + afronding / breedte

    talud = (checks.TANASTL + marge(checks.TANASTL, inst.min_taludbreedte),
             checks.TANAMAX - marge(checks.TANAMAX, inst.min_taludbreedte))
    berm = (checks.TANAMIN + marge(checks.TANAMIN, inst.min_bermbreedte),
            checks.TANAFLW - marge(checks.TANAFLW, inst.min_bermbreedte))
    voorland = checks.TANAMINVOORLAND - marge(checks.TANAMINVOORLAND, checks.DELTAXMINVOORLAND)
    for naam, (lo, hi) in (("talud", talud), ("berm", berm)):
        if not 0 < lo < hi:
            raise ValueError(
                f"Geen ruimte voor de helling van een {naam} na de marge voor afronden op {inst.decimalen} decimalen; "
                "kies meer decimalen of een grotere minimale breedte."
            )
    return HydraGrenzen(
        tan={"v": (-voorland, voorland), "t": talud, "b": berm, "k": (0.0, berm[1])},
        max_bermen=checks.NBERMEN,
        min_voorland=checks.DELTAXMINVOORLAND,
        voorland_tan=voorland,
    )


def controleer(x, z, kruinhoogte=None):
    """Controle van prflcheck voor een dijkdeel (teen tot buitenkruinlijn), zonder voorland; geeft ProfielError."""
    prflcheck = laad_prflcheck()
    punten = [prflcheck.ProfielPunt(float(a), float(b), 1.0) for a, b in zip(x, z)]
    kruinhoogte = float(z[-1]) if kruinhoogte is None else kruinhoogte
    return prflcheck.Profiel(dijk_punten=punten, kruinhoogte=kruinhoogte, damhoogte=0.0, richting=0.0).check()


def rdp(points, epsilon):
    """
    Vereenvoudig een lijn met het Ramer-Douglas-Peucker algoritme (zoals `rdp.rdp` uit het pakket rdp).

    Een punt blijft staan als het verder dan `epsilon` van de lijn tussen de omliggende bewaarde punten ligt.
    """
    points = np.asarray(points, dtype=float)
    keep = np.zeros(len(points), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last - first < 2:
            continue
        start, end = points[first], points[last]
        between = points[first + 1 : last]
        dx, dy = end - start
        norm = np.hypot(dx, dy)
        if norm == 0:
            dist = np.hypot(between[:, 0] - start[0], between[:, 1] - start[1])
        else:
            dist = np.abs(dx * (start[1] - between[:, 1]) - dy * (start[0] - between[:, 0])) / norm
        i = int(np.argmax(dist))
        if dist[i] > epsilon:
            index = first + 1 + i
            keep[index] = True
            stack += [(first, index), (index, last)]
    return points[keep]


def representatief_profiel(profielen, half, kwantiel=0.2, stap=0.5, min_dekking=0.5):
    """
    Eén profiel uit een groep profielen: per x (stappen van `stap`, van -half tot half) het `kwantiel` van de
    hoogtes. `profielen` is een lijst (d, z) met NaN zonder hoogte; waar minder dan `min_dekking` van de
    profielen een hoogte heeft, is het resultaat NaN.
    """
    x = np.arange(-half, half + stap / 2, stap)
    zs = []
    for d, z in profielen:
        d, z = np.asarray(d, dtype=float), np.asarray(z, dtype=float)
        ok = ~np.isnan(z)
        if ok.sum() >= 2:
            zs.append(np.interp(x, d[ok], z[ok], left=np.nan, right=np.nan))
    z = np.full(len(x), np.nan)
    if not zs:
        return x, z
    zs = np.array(zs)
    ok = np.mean(~np.isnan(zs), axis=0) >= min_dekking
    z[ok] = np.nanquantile(zs[:, ok], kwantiel, axis=0)
    return x, z


def zoek_kruin(x, z, zoekafstand):
    """
    Kruin: het hoogste punt van (x, z) met |x| <= `zoekafstand` (afstand tot de dijklijn); NaN telt niet mee.

    Bij gelijke hoogte het punt het verst naar buiten.
    """
    ok = ~np.isnan(z) & (np.abs(x) <= zoekafstand)
    if not ok.any():
        raise ValueError(f"geen AHN-hoogtes binnen {zoekafstand:g} m van de dijklijn")
    i = np.flatnonzero(ok)[np.argmax(z[ok])]
    return float(x[i]), float(z[i])


# ---------- Fit bij gegeven knikpunten (veel kandidaten tegelijk) ----------


def _bvls(AtA, Atb, btb, lo, hi):
    """
    Kleinste kwadraten met grenzen lo <= t <= hi voor N problemen tegelijk (actieve verzameling).

    AtA (N, m, m), Atb (N, m), btb (N). Geeft t (N, m) en de som van de gekwadrateerde residuen (N).
    """
    N, m = Atb.shape
    gelijk = lo == hi
    t = np.broadcast_to(np.where(gelijk, lo, np.clip(0.0, lo, hi)), (N, m)).copy()
    vrij = np.broadcast_to(~gelijk, (N, m)).copy()
    eye = np.eye(m)
    for _ in range(3 * m + 5):
        M = np.where(vrij[:, :, None] & vrij[:, None, :], AtA, eye) + 1e-10 * eye
        rhs = np.where(vrij, Atb - np.einsum("nij,nj->ni", AtA, np.where(vrij, 0.0, t)), t)
        nieuw = np.where(vrij, np.linalg.solve(M, rhs[..., None])[..., 0], t)
        buiten = vrij & ((nieuw < lo) | (nieuw > hi))
        t = np.clip(nieuw, lo, hi)
        vrij &= ~buiten
        # Vrijgeven waar de gradiënt van de grens af wijst (alleen problemen zonder overschrijding deze ronde)
        g = Atb - np.einsum("nij,nj->ni", AtA, t)
        los = (
            ~buiten.any(axis=1)[:, None]
            & ~vrij
            & ~gelijk
            & (((t <= lo) & (g > 1e-9)) | ((t >= hi) & (g < -1e-9)))
        )
        vrij |= los
        if not buiten.any() and not los.any():
            break
    sse = btb - 2 * np.einsum("ni,ni->n", t, Atb) + np.einsum("ni,nij,nj->n", t, AtA, t)
    return t, np.maximum(sse, 0.0)


class _Fit:
    """De fit van één representatief profiel (x, y = kruinhoogte - z) met vaste instellingen en grenzen."""

    def __init__(self, x, y, inst, grenzen):
        self.x, self.y, self.inst, self.grenzen = x, y, inst, grenzen

    def fit(self, knopen, soorten):
        """
        Fit voor N sets knikpunten (N, m+1) met dezelfde soorten delen.

        Het profiel loopt vanaf de kruintop (laatste knikpunt) naar buiten: z(x) = top - som(tan_j * overlap_j(x)).
        """
        knopen = np.atleast_2d(knopen)
        a, b = knopen[:, :-1], knopen[:, 1:]
        A = np.clip(b[:, None, :] - np.maximum(self.x[None, :, None], a[:, None, :]), 0.0, (b - a)[:, None, :])
        AtA = np.einsum("nkm,nkl->nml", A, A)
        Atb = np.einsum("nkm,k->nm", A, self.y)
        lo = np.array([self.grenzen.tan[s][0] for s in soorten])
        hi = np.array([self.grenzen.tan[s][1] for s in soorten])
        return _bvls(AtA, Atb, np.full(len(knopen), float(self.y @ self.y)), lo, hi)

    def kosten(self, knopen, soorten):
        """
        Som van de gekwadrateerde residuen per set knikpunten; oneindig als een talud naast een berm lager is dan
        `min_taludhoogte` (zo zoekt de fit alleen naar bermen die mee kunnen tellen).
        """
        knopen = np.atleast_2d(knopen)
        tan, sse = self.fit(knopen, soorten)
        if "b" in soorten:
            talud = np.array([s == "t" for s in soorten])
            hoogte = (tan * np.diff(knopen, axis=1))[:, talud]
            sse = np.where(np.all(hoogte >= self.inst.min_taludhoogte - 1e-9, axis=1), sse, np.inf)
        return sse

    def min_breedte(self, soort):
        return {"v": 0.0, "t": self.inst.min_taludbreedte, "b": self.inst.min_bermbreedte, "k": 0.0}[soort]

    def geldig(self, knopen, soorten):
        breedte = np.array([self.min_breedte(s) for s in soorten])
        return np.all(np.diff(knopen, axis=-1) >= breedte - 1e-9, axis=-1)

    def optimaliseer(self, knopen, soorten, rondes=8):
        """Verschuif de knikpunten één voor één, en bermen als geheel, zolang de fit beter wordt."""
        stap = self.inst.stap
        knopen = np.array(knopen, dtype=float)
        beste = self.kosten(knopen, soorten)[0]

        def probeer(trial):
            nonlocal beste, knopen
            trial = trial[self.geldig(trial, soorten)]
            if len(trial) == 0:
                return False
            sse = self.kosten(trial, soorten)
            k = int(np.argmin(sse))
            if sse[k] < beste - 1e-9:
                beste, knopen = sse[k], trial[k].copy()
                return True
            return False

        for _ in range(rondes):
            verbeterd = False
            # Elk knikpunt (behalve begin en kruintop) op alle toegestane posities
            for i in range(1, len(knopen) - 1):
                lo = knopen[i - 1] + self.min_breedte(soorten[i - 1])
                hi = knopen[i + 1] - self.min_breedte(soorten[i])
                posities = np.arange(np.ceil(lo / stap - 1e-9) * stap, hi + 1e-9, stap)
                trial = np.repeat(knopen[None, :], len(posities), axis=0)
                trial[:, i] = posities
                verbeterd |= probeer(trial)
            # Bermen (beide knikpunten) samen verschuiven, met veelvouden van de stap
            for j, s in enumerate(soorten):
                if s == "b":
                    shifts = stap * np.array([-8, -4, -2, -1, 1, 2, 4, 8])
                    trial = np.repeat(knopen[None, :], len(shifts), axis=0)
                    trial[:, j] += shifts
                    trial[:, j + 1] += shifts
                    verbeterd |= probeer(trial)
            if not verbeterd:
                break
        return knopen, beste

    def voeg_berm_in(self, knopen, soorten, n_beste=3):
        """Voeg een berm in (op elk talud, of in het voorland met een nieuw onderste talud); optimaliseer de besten."""
        nv = self.inst.voorland_delen
        talud = self.inst.min_taludbreedte
        raster = self.inst.stap * max(1, round(1.0 / self.inst.stap))  # ~1 m, op het raster van de knikpunten
        breedtes = sorted({max(b, self.inst.min_bermbreedte) for b in (3, 5, 8, 12, 18)})
        groepen = {}  # soorten -> lijst met knikpunten
        for j, s in enumerate(soorten):
            if s != "t":
                continue
            a, b = knopen[j], knopen[j + 1]
            for breedte in breedtes:
                for start in np.arange(a + talud, b - talud - breedte + 1e-9, raster):
                    kn = np.concatenate([knopen[: j + 1], [start, start + breedte], knopen[j + 1 :]])
                    groepen.setdefault(soorten[: j + 1] + "bt" + soorten[j + 1 :], []).append(kn)
        # Berm in het (vermeende) voorland: de teen schuift naar buiten, met een nieuw onderste talud
        for breedte in breedtes:
            for onder in sorted({max(o, talud) for o in (3, 6, 10)}):
                for start in np.arange(knopen[nv - 1] + onder, knopen[nv] - breedte + 1e-9, raster):
                    kn = np.concatenate([knopen[:nv], [start - onder, start, start + breedte], knopen[nv + 1 :]])
                    groepen.setdefault(soorten[:nv] + "tbt" + soorten[nv + 1 :], []).append(kn)
        proeven = []
        for ty, kns in groepen.items():
            kns = np.array(kns)
            kns = kns[self.geldig(kns, ty)]
            if len(kns) == 0:
                continue
            sse = self.kosten(kns, ty)
            proeven += [(sse[i], kns[i], ty) for i in np.argsort(sse)[:n_beste] if np.isfinite(sse[i])]
        proeven.sort(key=lambda p: p[0])
        beste = None
        for _, kn, ty in proeven[:n_beste]:
            kn, sse = self.optimaliseer(kn, ty)
            if np.isfinite(sse) and (beste is None or sse < beste[1]):
                beste = (kn, sse, ty)
        return beste

    def zonder_berm(self, kruin_x):
        """0 bermen: voorland | talud | kruin, vanaf een paar beginliggingen van de teen."""
        nv, stap, talud = self.inst.voorland_delen, self.inst.stap, self.inst.min_taludbreedte
        soorten = "v" * nv + "tk"
        x0, beste = self.x[0], None
        for f in (0.2, 0.5, 0.8):
            teen = max(np.round((x0 + f * (kruin_x - x0 - talud)) / stap) * stap, x0)
            voorland = np.round(np.linspace(x0, teen, nv + 1) / stap) * stap
            voorland[0] = x0
            kn = np.concatenate([voorland, [max(teen + talud, kruin_x - 2), kruin_x]])
            if not self.geldig(kn, soorten):
                continue
            kn, sse = self.optimaliseer(kn, soorten)
            if beste is None or sse < beste[1]:
                beste = (kn, sse, soorten)
        if beste is None:
            raise ValueError(f"geen buitentalud van minstens {talud:g} m breed gevonden")
        return beste


def fit_profiel(x, z, kruin_x, kruin_z, inst=None):
    """
    Fit voorland | talud | (berm | talud) x k | kruin op het representatieve profiel (x, z) tot de kruintop.

    Geeft (gekozen, modellen): per aantal bermen k een dict met 'knopen', 'z' (op de knikpunten), 'soorten',
    'tan', 'rmse', 'geldig' (taluds hoog genoeg) en 'dijk_x'/'dijk_z' (teen tot buitenkruinlijn); `gekozen` is het
    aantal bermen volgens de bermcriteria in `inst`.
    """
    inst = inst or FitInstellingen()
    grenzen = hydra_grenzen(inst)
    nv = inst.voorland_delen
    ok = ~np.isnan(z) & (x <= kruin_x)
    x, z = x[ok], z[ok]
    if len(x) < 2 or kruin_x - x[0] < inst.min_taludbreedte:
        raise ValueError("te weinig AHN-hoogtes aan de buitenzijde van de kruin")

    # Eerst de teen globaal (zonder berm), daarna alles opnieuw met alleen het voorland tot `voorland_fit` plus de
    # breedte van die eerste dijk buiten die teen (bij bermen ligt de echte teen verder naar buiten).
    teen = _Fit(x, kruin_z - z, inst, grenzen).zonder_berm(kruin_x)[0][nv]
    venster = x >= teen - inst.voorland_fit - (kruin_x - teen)
    x, z = x[venster], z[venster]
    fitter = _Fit(x, kruin_z - z, inst, grenzen)
    oplossingen = {0: fitter.zonder_berm(kruin_x)}
    for k in range(1, grenzen.max_bermen + 1):
        vorige = oplossingen[k - 1]
        nieuw = fitter.voeg_berm_in(vorige[0], vorige[2])
        if nieuw is None:
            break
        oplossingen[k] = nieuw

    modellen = {}
    for k, (kn, _, soorten) in oplossingen.items():
        tan = fitter.fit(kn, soorten)[0][0]
        zk = kruin_z - np.concatenate([np.cumsum((tan * np.diff(kn))[::-1])[::-1], [0.0]])
        taluds = [zk[j + 1] - zk[j] for j, s in enumerate(soorten) if s == "t"]
        modellen[k] = {
            "knopen": kn,
            "z": zk,
            "soorten": soorten,
            "tan": tan,
            "geldig": k == 0 or min(taluds) >= inst.min_taludhoogte,
            # Dijkpunten: van de teen tot de buitenkruinlijn
            "dijk_x": kn[nv:-1],
            "dijk_z": zk[nv:-1],
        }
    # RMSE over het dijkdeel: van de buitenste teen van de modellen tot de kruin, voor alle modellen hetzelfde
    # bereik. Over het hele bereik zou een lang, ruw voorland de winst van een berm verdunnen.
    dijk = x >= min(m["dijk_x"][0] for m in modellen.values())
    for m in modellen.values():
        m["rmse"] = float(np.sqrt(np.mean((np.interp(x[dijk], m["knopen"], m["z"]) - z[dijk]) ** 2)))

    gekozen = 0
    for k in range(1, grenzen.max_bermen + 1):
        if k not in modellen or not modellen[k]["geldig"]:
            continue
        vorige, rmse = modellen[gekozen]["rmse"], modellen[k]["rmse"]
        if rmse <= (1 - inst.berm_winst_rel) * vorige and vorige - rmse >= inst.berm_winst_abs:
            gekozen = k
    return gekozen, modellen


def voorland_punten(x, z, teen_x, teen_z, inst=None, eps=0.25):
    """
    Voorland van het begin van het representatieve profiel tot de teen, volgens de regels van prflcheck.

    Vereenvoudigd met RDP (`eps`), punten minstens DELTAXMINVOORLAND (10 m) uit elkaar, hellingen hooguit
    TANAMINVOORLAND (1:10) en geen punt lager dan het eerste punt. Ligt een later punt lager, dan zakt het eerste
    punt naar dat niveau (voorzichtig: dieper water aan het begin van het voorland). Geeft (punten (n, 2),
    verlaging van het eerste punt [m]), of (None, 0) als het voorland te kort is.
    """
    inst = inst or FitInstellingen()
    grenzen = hydra_grenzen(inst)
    min_afstand, tan = grenzen.min_voorland, grenzen.voorland_tan
    ok = ~np.isnan(z) & (x < teen_x)
    xs = np.append(x[ok], teen_x)
    zs = np.append(z[ok], teen_z)
    if teen_x - xs[0] < min_afstand:
        return None, 0.0
    punten = rdp(np.column_stack([xs, zs]), eps)
    # Van de teen naar buiten: punten te dicht bij het vorige weglaten
    houd = [len(punten) - 1]
    for i in range(len(punten) - 2, -1, -1):
        if punten[houd[-1], 0] - punten[i, 0] >= min_afstand:
            houd.append(i)
    punten = punten[houd[::-1]]
    # Hellingen begrenzen, vanaf de teen naar buiten (de teen blijft liggen)
    for i in range(len(punten) - 2, -1, -1):
        dx = punten[i + 1, 0] - punten[i, 0]
        punten[i, 1] = np.clip(punten[i, 1], punten[i + 1, 1] - tan * dx, punten[i + 1, 1] + tan * dx)
    # Geen punt lager dan het eerste: verlaag het eerste punt; een te steil eerste deel wordt samengevoegd
    eerste = punten[0, 1]
    punten[0, 1] = min(eerste, punten[1:, 1].min())
    while len(punten) > 2 and punten[1, 1] - punten[0, 1] > tan * (punten[1, 0] - punten[0, 0]):
        punten = np.delete(punten, 1, axis=0)
    punten[0, 1] = max(punten[0, 1], punten[1, 1] - tan * (punten[1, 0] - punten[0, 0]))
    punten = punten.round(inst.decimalen)
    return punten, round(max(float(eerste - punten[0, 1]), 0.0), inst.decimalen) + 0.0

import numpy as np
import rdp

from .constants import *


class SchemProfile:
    @staticmethod
    def maak_geschematiseerd_profiel(
        profiel_groep, xstart, xend, nodata=NODATA, quantile=0.2, xstep=0.1, rdp_eps=0.2, min_hydra_step=2
    ):
        if min_hydra_step < xstep:
            raise ValueError(f"min_hydra_step ({min_hydra_step}) is groter dan xstep ({xstep}), mag niet")
        if int(min_hydra_step / xstep) != (min_hydra_step / xstep):
            raise ValueError(f"xstep ({xstep}) is geen meervoud (integer) van min_hydra_step ({min_hydra_step})")

        xlijn_simp, ylijn_simp, clean_profielen = None, None, None
        if len(profiel_groep) > 0:
            clean_profielen = []

            # Maak een regelmatig x-grid voor de profielen
            x = np.arange(xstart, xend + xstep, xstep).round(decimals=2)

            # Bepaal de y waardes van de profielen voor het regelmatige x-grid
            yv = np.zeros((len(x), len(profiel_groep)))
            for i, row in enumerate(profiel_groep.itertuples()):
                xyz = np.array(row.geometry.coords)
                # centreer op middelpunt
                d = np.sqrt((xyz[:, 0] - xyz[0, 0]) ** 2 + (xyz[:, 1] - xyz[0, 1]) ** 2)
                d_mid = np.sqrt((xyz[0, 0] - row.mid_x) ** 2 + (xyz[0, 1] - row.mid_y) ** 2)
                d = d - d_mid
                z = xyz[:, 2]

                # vervang nodata door nan
                z[z == nodata] = np.nan

                # Drop nans.
                d = d[~np.isnan(z)]
                z = z[~np.isnan(z)]

                clean_profielen.append((d, z))

                # Regulier grid
                if len(d) > 0:
                    yv[:, i] = np.interp(x, d, z)
                else:
                    yv[:, i] = np.nan

            # Drop kolommen met alleen NaN
            yv = yv[:, ~np.all(np.isnan(yv), axis=0)]

            # Maak een geschematiseerd profiel met stappen van 2 meter voor
            # het eerder gekozen regelmatige x-grid
            xbase = np.arange(xstart, xend - min_hydra_step + xstep, min_hydra_step)
            xdiffs = np.arange(0, min_hydra_step, xstep).round(decimals=2)

            if yv.shape[1] == 0:
                clean_profielen = None
            else:
                # De stappen van 2 meter zijn vrij grof, schuif met xstep en bekijk
                # welke xwaardes voor het
                # Bepaal de y waardes op basis van de gemiddelde y waardes
                xlijnv = np.zeros((len(xbase), len(xdiffs)))
                ylijnv = np.zeros((len(xbase), len(xdiffs)))
                dists = np.zeros(len(xdiffs))
                for i, xdiff in enumerate(xdiffs):
                    # Verschuif de x coordinaten ietwat
                    xlijnv[:, i] = (xbase + xdiff).round(decimals=2)

                    # Vindt de corresponderende y coordinaten voor xlijn
                    ind = np.isin(x, xlijnv[:, i])

                    # Bepaal de y waardes van het geschematiseerde profiel op basis van het
                    # gegeven kwantiel.
                    ylijnv[:, i] = np.quantile(yv[ind, :], quantile, axis=1)

                    # bepaal de gesommeerde kwadratische afstand tussen de y waardes en de profielen
                    xlijn_finegrid = np.arange(xlijnv[0, i], xlijnv[-1, i] + xstep, xstep).round(decimals=2)
                    ylijn_finegrid = np.interp(xlijn_finegrid, xlijnv[:, i], ylijnv[:, i])
                    ind = np.isin(x, xlijn_finegrid)
                    dists[i] = np.sum((yv[ind, :] - ylijn_finegrid[:, None]) ** 2)

                # Definitieve lijn op basis van de minimale afstand
                if len(dists) > 0:
                    min_ind = np.argmin(dists)
                    xlijn = xbase + xdiffs[min_ind]
                    ylijn = ylijnv[:, min_ind]
                else:
                    xlijn = xbase.copy()
                    ylijn = []

                # Vereenvoudig definitieve lijn met rdp
                if len(ylijn) > 0:
                    rdp_vec = rdp.rdp(np.array([xlijn, ylijn]).T, epsilon=rdp_eps)
                    xlijn_simp = rdp_vec[:, 0]
                    ylijn_simp = rdp_vec[:, 1]
                else:
                    xlijn_simp = xlijn
                    ylijn_simp = ylijn

        return xlijn_simp, ylijn_simp, clean_profielen

    @staticmethod
    def __maak_profiel_stap(x_hydra, y_hydra, hydra_step=2, xstep=0.1, rdp_eps=0.2):
        # Maximaal aantal stappen
        max_steps = int(np.floor((x_hydra.max() - x_hydra.min()) / hydra_step))

        # Probeer ook minder stappen, maar minimaal 2.
        nsteps = max_steps - np.arange(0, 10, 1)
        nsteps = nsteps[nsteps >= 2]

        # Als er geen stappen gevonden zijn, zijn waarschijnlijk maar 2 punten
        # aanwezig die xafstand < 2 hebben.
        if len(nsteps) == 0:
            slope = (y_hydra.max() - y_hydra.min()) / (x_hydra.max() - x_hydra.min())
            y_hydra = np.array([y_hydra.min(), y_hydra.min() + slope * hydra_step])
            x_hydra = np.array([x_hydra.min(), x_hydra.min() + hydra_step])
            nsteps = np.array([2])

        # Bepaal de minimale gesommeerde kwadratische afstand voor verschillende stapgroottes.
        xfine = np.arange(x_hydra.min(), x_hydra.max() + xstep, xstep).round(decimals=2)
        yfine = np.interp(xfine, x_hydra, y_hydra)
        dists = np.zeros(len(nsteps))
        for i, steps in enumerate(nsteps):
            xlijn = np.linspace(x_hydra.min(), x_hydra.max(), steps, endpoint=True)
            ylijn = np.interp(xlijn, x_hydra, y_hydra)
            dists[i] = np.sum((np.interp(xfine, xlijn, ylijn) - yfine) ** 2)

        # Definitieve lijn
        steps = nsteps[np.argmin(dists)]
        xlijn = np.linspace(x_hydra.min(), x_hydra.max(), steps, endpoint=True)
        ylijn = np.interp(xlijn, x_hydra, y_hydra)

        # Vereenvoudig definitieve lijn met rdp
        rdp_vec = rdp.rdp(np.array([xlijn, ylijn]).T, epsilon=rdp_eps)
        xlijn_simp = rdp_vec[:, 0]
        ylijn_simp = rdp_vec[:, 1]

        return xlijn_simp, ylijn_simp

    @staticmethod
    def __bepaal_helling(xbuiten, ybuiten, eps_talud, verbose):
        # hellingen
        xdiff = xbuiten[1:] - xbuiten[0:-1]
        ydiff = ybuiten[1:] - ybuiten[0:-1]

        # 0 hellingen zorgen voor problemen, geef deze een zeer flauwe helling
        zero_inds = np.flatnonzero(ydiff == 0)
        for ind in zero_inds:
            if verbose:
                print("Pas 0-helling aan")
            ydiff[ind:] += eps_talud

        # 0 afstanden zorgen voor problemen, geef deze een zeer kleine afstand
        zero_inds = np.flatnonzero(xdiff == 0)
        for ind in zero_inds:
            if verbose:
                print("Pas 0-afstand aan")
            xdiff[ind:] += eps_talud

        # Bepaal hellingen
        slopes = xdiff / ydiff

        return slopes

    @staticmethod
    def maak_hydra_profiel(
        xlijn_simp,
        ylijn_simp,
        cut_off_buitentalud=None,
        eps_talud=1e-7,
        verbose=False,
        rdp_eps=0.2,
        hydra_step=2,
        hist_slopes=None,
    ):

        # buitentalud
        if cut_off_buitentalud is not None:
            ind_buiten = xlijn_simp <= cut_off_buitentalud
            xbuiten = xlijn_simp[ind_buiten]
            ybuiten = ylijn_simp[ind_buiten]
        else:
            xbuiten = xlijn_simp.copy()
            ybuiten = ylijn_simp.copy()

        # Bepaal hellingen
        slopes = SchemProfile.__bepaal_helling(xbuiten, ybuiten, eps_talud, verbose)

        # Initialiseer historie
        if hist_slopes is None:
            hist_slopes = [("Initieel", xbuiten.copy(), ybuiten.copy(), slopes.copy())]

        # Negatieve hellingen op het einde duiden op binnentalud, negeer deze.
        while len(slopes) > 0 and slopes[-1] < 0:
            msg = "Verwijder binnentalud"
            if verbose:
                print(msg)
            xbuiten = xbuiten[:-1]
            ybuiten = ybuiten[:-1]
            slopes = slopes[:-1]
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Negatieve hellingen aan het begin is rommel, negeer deze.
        while len(slopes) > 0 and slopes[0] < 0:
            msg = "Verwijder negatief eerste buitentalud"
            if verbose:
                print(msg)
            xbuiten = xbuiten[1:]
            ybuiten = ybuiten[1:]
            slopes = slopes[1:]
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Check of ergens anders negatieve strekkingen zijn
        neg_ind = np.flatnonzero(slopes < 0)
        while len(neg_ind) > 0:
            ind = neg_ind[-1]

            msg = "Strekking negatief, verander naar 1:100 berm"
            if verbose:
                print(msg)
            target_y = ybuiten[ind]
            # print(target_y)
            target_x = None
            if ybuiten[(ind + 1) :].max() >= target_y:
                for i in range(ind, len(xbuiten) - 1):
                    if ybuiten[i + 1] >= target_y:
                        target_x = np.interp(target_y, [ybuiten[i], ybuiten[i + 1]], [xbuiten[i], xbuiten[i + 1]])
                        break
            else:
                # Geen intersectie mogelijk, verleng laatste strekking
                target_x = np.interp(target_y, ybuiten[-2:], xbuiten[-2:])
            if target_x is None:
                raise ValueError("Kan geen correcte x2 waarde vinden")

            if (target_y < ybuiten[ind + 2]) and (ind + 2 == len(xbuiten) - 1):
                xbuiten[ind + 2] = target_x + (ybuiten[ind + 2] - target_y) * 100
            else:
                xbuiten[ind + 2] = target_x
                ybuiten[ind + 2] = target_y

            target_y = None
            target_ind = None
            if ind == 0:
                print("LOEEE")
            else:
                for i in range(ind - 1, -1, -1):
                    # a*x+b=b*x+d
                    #  - a=1/s1, b = 1/s2
                    #  - b=y1-(x1/s1)
                    #  - d=y2-(x2/s2)
                    # x=(d-b)/(1/s1-1/s2)
                    b = ybuiten[i] - xbuiten[i] / slopes[i]
                    d = ybuiten[ind + 2] - xbuiten[ind + 2] / 100
                    target_x = (d - b) / (1 / slopes[i] - 1 / 100)
                    if target_x >= xbuiten[i] and target_x <= xbuiten[i + 1]:
                        target_y = np.interp(target_x, [xbuiten[i], xbuiten[i + 1]], [ybuiten[i], ybuiten[i + 1]])
                        target_ind = i + 1
                        break
                    elif i == 0:
                        # Geen match, verander het eerste punt
                        target_y = ybuiten[0 : ind + 2].min()
                        target_x = 100 * (target_y - ybuiten[ind + 2])
                        target_ind = 0
            if target_y is None:
                raise ValueError("Kan geen correcte x1 waarde vinden")
            xbuiten[target_ind] = target_x
            ybuiten[target_ind] = target_y
            slopes[target_ind] = 100

            # Maak nieuwe slopes en x/y
            xbuiten = np.concatenate((xbuiten[0 : target_ind + 1], xbuiten[ind + 2 :]))
            ybuiten = np.concatenate((ybuiten[0 : target_ind + 1], ybuiten[ind + 2 :]))
            slopes = np.concatenate((slopes[0 : target_ind + 1], slopes[ind + 2 :]))
            neg_ind = np.flatnonzero(slopes < 0)
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Mag niet beginnen met een berm.
        while len(slopes) > 0 and slopes[0] >= 15:
            if len(slopes) == 1:
                msg = "Profiel is berm, verander naar 1:8 talud"
                if verbose:
                    print(msg)
                xbuiten[-2] = xbuiten[-1] - (ybuiten[-1] - ybuiten[-2]) * 8
                slopes[-1] = 8
            else:
                msg = "Profiel begint met berm, trek tweede talud door"
                if verbose:
                    print(msg)
                # Trek tweede talud door
                add_y = ybuiten[0] - ybuiten[1]
                add_x = add_y * slopes[1]
                xbuiten[1] += add_x
                ybuiten[1] += add_y
                # Verwijder eerste talud
                xbuiten = xbuiten[1:]
                ybuiten = ybuiten[1:]
                slopes = slopes[1:]
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Check of er strekkingen zijn die flauwer dan 1:100 zijn
        too_flat = np.flatnonzero(slopes > 100)
        for ind in too_flat:
            msg = "Strekking te flauw, verander naar 1:100 berm"
            if verbose:
                print(msg)
            x_add = 100 * (ybuiten[ind + 1] - ybuiten[ind]) - (xbuiten[ind + 1] - xbuiten[ind])
            xbuiten[(ind + 1) :] += x_add
            # shift alles zodat kruin op de goede plek blijft liggen
            xbuiten -= x_add
            slopes = SchemProfile.__bepaal_helling(xbuiten, ybuiten, eps_talud, verbose)
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Check of er strekkingen zijn die steiler zijn dan 1:1
        too_steep = np.flatnonzero((slopes < 1) & (slopes > 0))
        for ind in too_steep:
            msg = "Strekking te steil, verander naar 1:1 talud"
            if verbose:
                print(msg)
            x_add = 1 * (ybuiten[ind + 1] - ybuiten[ind]) - (xbuiten[ind + 1] - xbuiten[ind])
            xbuiten[(ind + 1) :] += x_add
            # shift alles zodat kruin op de goede plek blijft liggen
            xbuiten -= x_add
            slopes = SchemProfile.__bepaal_helling(xbuiten, ybuiten, eps_talud, verbose)
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Check of strekkingen tussen 1:8 en 1:15 liggen.
        undecided_inds = np.flatnonzero((slopes > 8) & (slopes < 15))
        mid = (15 - 8) / 2 + 8
        for ind in undecided_inds:
            if slopes[ind] - mid >= 0:
                msg = "Strekking tussen talud en berm, verander naar 1:8 talud"
                if verbose:
                    print(msg)
                x_add = 7.99 * (ybuiten[ind + 1] - ybuiten[ind]) - (xbuiten[ind + 1] - xbuiten[ind])
            else:
                msg = "Strekking tussen talud en berm, verander naar 1:15 berm"
                if verbose:
                    print(msg)
                x_add = 15.01 * (ybuiten[ind + 1] - ybuiten[ind]) - (xbuiten[ind + 1] - xbuiten[ind])
            xbuiten[(ind + 1) :] += x_add
            # shift alles zodat kruin op de goede plek blijft liggen
            xbuiten -= x_add
            slopes = SchemProfile.__bepaal_helling(xbuiten, ybuiten, eps_talud, verbose)
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Mag niet eindigen met een berm, en liefst niet met een tuimeldijk
        while len(slopes) > 0:
            fix_slope = 0
            msg = ""
            if slopes[-1] >= 15:
                # Mag niet eindigen met een berm.
                msg = "Profiel eindigt met berm, trek een-na-laatste talud door"
                if verbose:
                    print(msg)
                fix_slope = -1

            if fix_slope == 0 and len(slopes) >= 2 and (ybuiten[-1] - ybuiten[-2]) < 1.0:
                # Laatste talud is minder dan een meter hoog, check of we dit kunnen doortrekken.
                if slopes[-1] > slopes[-2] and slopes[-2] <= 8:
                    # Flauwer dan een-na-laatste talud, en een-na-laatste talud is steiler dan 1:8
                    msg = "Laatste talud <1m en flauwer dan een-na-laaste talud, trek een-na-laaste talud door"
                    if verbose:
                        print(msg)
                    fix_slope = -1
                if len(slopes) >= 3 and slopes[-2] >= 15 and slopes[-3] <= 8:
                    # een-na-laatste talud is een berm, trek twee-na-laaste talud door
                    msg = "Laatste talud <1m, een-na-laatste talud is een berm, trek twee-na-laatste talud door"
                    if verbose:
                        print(msg)
                    fix_slope = -2

            if fix_slope != 0 and len(slopes) >= (abs(fix_slope) + 1):
                # Trek talud door
                i1 = -1
                i2 = fix_slope - 1
                add_y = ybuiten[i1] - ybuiten[i2]
                add_x = add_y * slopes[i2]
                xbuiten[i2] += add_x
                ybuiten[i2] += add_y
                # Verwijder laatste talud(s)
                xbuiten = xbuiten[:fix_slope]
                ybuiten = ybuiten[:fix_slope]
                slopes = slopes[:fix_slope]
                hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))
            else:
                break

        # Maximaal 2 bermen in profiel.
        n_berms = np.flatnonzero(slopes >= 15)
        if len(n_berms) > 2:
            msg = "Meer dan 2 bermen, bewaar alleen de laatste 2 bermen"
            if verbose:
                print(msg)
            xbuiten = xbuiten[n_berms[-2] :]
            ybuiten = ybuiten[n_berms[-2] :]
            slopes = slopes[n_berms[-2] :]
            hist_slopes.append((msg, xbuiten.copy(), ybuiten.copy(), slopes.copy()))

        # Controleer stepsize.
        diff = np.diff(xbuiten).round(decimals=2)
        while (diff < 2).any():
            x_simp2, y_simp2 = SchemProfile.__maak_profiel_stap(
                xbuiten, ybuiten, rdp_eps=rdp_eps, hydra_step=hydra_step
            )
            xbuiten, ybuiten, slopes, hist_slopes = SchemProfile.maak_hydra_profiel(
                x_simp2,
                y_simp2,
                cut_off_buitentalud=None,
                eps_talud=eps_talud,
                verbose=verbose,
                hist_slopes=hist_slopes,
            )
            diff = np.diff(xbuiten).round(decimals=2)

        return xbuiten, ybuiten, slopes, hist_slopes

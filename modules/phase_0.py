import os
import numpy as np
import pandas as pd
import networkx as nx
import osmnx as ox
import streamlit as st

from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter


# -----------------------------------------------------------------------------
# OUTILS
# -----------------------------------------------------------------------------

def normalize_text(x):
    return str(x).strip().lower()


def find_column(columns, keywords, default=None):
    cols = list(columns)
    for c in cols:
        c_norm = normalize_text(c)
        if any(k in c_norm for k in keywords):
            return c
    return default


# -----------------------------------------------------------------------------
# GEOCODAGE
# -----------------------------------------------------------------------------

def geocoder_sites(df_param_sites):
    df_param_sites = df_param_sites.copy()
    df_param_sites.columns = [str(c).strip() for c in df_param_sites.columns]

    if len(df_param_sites.columns) < 2:
        st.error("L'onglet des sites doit contenir au moins 2 colonnes : nom du site et adresse.")
        return df_param_sites, [{"Erreur": "Colonnes insuffisantes"}]

    col_nom = df_param_sites.columns[0]
    col_adresse = df_param_sites.columns[1]

    geolocator = Nominatim(user_agent="chu_nantes_logistique")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

    lats, lons = [], []
    adresses_en_echec = []

    for _, row in df_param_sites.iterrows():
        nom = str(row[col_nom]).strip()
        addr = str(row[col_adresse]).strip()

        if not addr or addr.lower() == "nan":
            lats.append(None)
            lons.append(None)
            adresses_en_echec.append({
                "Site": nom,
                "Adresse": addr,
                "Erreur": "Adresse vide"
            })
            continue

        full_addr = addr if "france" in addr.lower() else f"{addr}, France"

        try:
            location = geocode(full_addr)

            if location is None:
                location = geocode(addr)

            if location:
                lats.append(location.latitude)
                lons.append(location.longitude)
            else:
                lats.append(None)
                lons.append(None)
                adresses_en_echec.append({
                    "Site": nom,
                    "Adresse": addr,
                    "Erreur": "Non trouvé"
                })

        except Exception as e:
            lats.append(None)
            lons.append(None)
            adresses_en_echec.append({
                "Site": nom,
                "Adresse": addr,
                "Erreur": str(e)
            })

    df_param_sites["Latitude"] = lats
    df_param_sites["Longitude"] = lons

    return df_param_sites, adresses_en_echec


# -----------------------------------------------------------------------------
# GRAPHE ROUTIER
# -----------------------------------------------------------------------------

def initialiser_graphe_routier(ville_ou_zone="Nantes, France"):
    cache_path = "./data/graph_routier.graphml"

    if os.path.exists(cache_path):
        try:
            return ox.load_graphml(cache_path)
        except Exception:
            pass

    try:
        st.info("Téléchargement de la carte routière...")
        G = ox.graph_from_address(ville_ou_zone, dist=30000, network_type="drive")
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)

        if not os.path.exists("./data"):
            os.makedirs("./data")

        ox.save_graphml(G, cache_path)
        return G

    except Exception as e:
        st.error(f"Erreur OSM : {e}")
        return None


# -----------------------------------------------------------------------------
# MATRICES DISTANCES / TEMPS
# -----------------------------------------------------------------------------

def calculer_matrice_hors_ligne(G, df_param_sites):
    if G is None:
        st.error("Le graphe routier est vide.")
        return None, None, None

    df_gps, erreurs = geocoder_sites(df_param_sites)

    if erreurs:
        st.session_state["geocoding_errors"] = erreurs
    else:
        st.session_state["geocoding_errors"] = []

    df_valides = df_gps.dropna(subset=["Latitude", "Longitude"]).copy()

    if df_valides.empty:
        st.error("❌ Aucun site n'a pu être localisé.")
        return None, None, None

    nodes = []
    mapping_site_index = {}
    nom_col_site = df_valides.columns[0]

    for _, row in df_valides.iterrows():
        site_name = str(row[nom_col_site]).strip()

        try:
            node = ox.nearest_nodes(
                G,
                X=float(row["Longitude"]),
                Y=float(row["Latitude"])
            )
            mapping_site_index[site_name] = len(nodes)
            nodes.append(node)
        except Exception:
            continue

    num_n = len(nodes)

    if num_n == 0:
        st.error("Aucun site n'a pu être projeté sur le graphe routier.")
        return None, None, None

    mat_dist = np.zeros((num_n, num_n))
    mat_temps = np.zeros((num_n, num_n))

    for i in range(num_n):
        for j in range(num_n):
            if i == j:
                continue
            try:
                mat_temps[i, j] = nx.shortest_path_length(
                    G, nodes[i], nodes[j], weight="travel_time"
                ) / 60
                mat_dist[i, j] = nx.shortest_path_length(
                    G, nodes[i], nodes[j], weight="length"
                ) / 1000
            except Exception:
                mat_temps[i, j] = 999
                mat_dist[i, j] = 999

    return mat_dist, mat_temps, mapping_site_index


# -----------------------------------------------------------------------------
# JOBS
# -----------------------------------------------------------------------------

def generer_jobs_atomises(df_flux, mapping_site_index, matrice_dist, matrice_temps, capa_max):
    if df_flux is None or df_flux.empty:
        st.error("Le DataFrame de flux est vide.")
        return pd.DataFrame()

    if capa_max is None or capa_max <= 0:
        st.error("La capacité véhicule doit être strictement positive.")
        return pd.DataFrame()

    df_flux = df_flux.copy()
    df_flux.columns = [str(c).replace("\n", " ").strip() for c in df_flux.columns]

    col_dep = find_column(df_flux.columns, ["départ", "depart", "origine", "site départ", "site depart"])
    col_arr = find_column(df_flux.columns, ["destination", "arrivée", "arrivee", "site arrivée", "site arrivee"])
    col_vol = find_column(df_flux.columns, ["volume", "quantité", "quantite", "charge", "flux"])

    if col_dep is None:
        st.error(f"Impossible de trouver la colonne de départ/origine. Colonnes disponibles : {list(df_flux.columns)}")
        return pd.DataFrame()

    if col_arr is None:
        st.error(f"Impossible de trouver la colonne de destination/arrivée. Colonnes disponibles : {list(df_flux.columns)}")
        return pd.DataFrame()

    if col_vol is None:
        st.error(f"Impossible de trouver la colonne de volume/quantité. Colonnes disponibles : {list(df_flux.columns)}")
        return pd.DataFrame()

    col_h_dep = find_column(
        df_flux.columns,
        ["heure de mise à disposition min départ", "heure de mise a disposition min depart", "heure départ", "heure depart"],
        default=None
    )
    col_h_arr = find_column(
        df_flux.columns,
        ["heure de livraison à destination", "heure de livraison a destination", "heure arrivée", "heure arrivee"],
        default=None
    )

    jobs = []
    ignored = []

    for idx, flux in df_flux.iterrows():
        try:
            orig_name = str(flux[col_dep]).strip()
            dest_name = str(flux[col_arr]).strip()

            if orig_name not in mapping_site_index or dest_name not in mapping_site_index:
                ignored.append({
                    "index_flux": idx,
                    "origine": orig_name,
                    "destination": dest_name,
                    "motif": "Site absent du mapping"
                })
                continue

            i = mapping_site_index[orig_name]
            j = mapping_site_index[dest_name]

            vol_tot = pd.to_numeric(flux[col_vol], errors="coerce")
            if pd.isna(vol_tot) or vol_tot <= 0:
                ignored.append({
                    "index_flux": idx,
                    "origine": orig_name,
                    "destination": dest_name,
                    "motif": "Volume nul ou invalide"
                })
                continue

            nb_splits = int(np.ceil(vol_tot / capa_max))

            for s in range(nb_splits):
                if s < nb_splits - 1:
                    v_unit = capa_max
                else:
                    reste = vol_tot % capa_max
                    v_unit = reste if reste != 0 else capa_max

                jobs.append({
                    "id_job": f"J_{idx}_{s}",
                    "origine": orig_name,
                    "destination": dest_name,
                    "volume": float(v_unit),
                    "dist_km": float(matrice_dist[i, j]),
                    "temps_min": float(matrice_temps[i, j]),
                    "h_dep": flux[col_h_dep] if col_h_dep else "08:00",
                    "h_arr": flux[col_h_arr] if col_h_arr else "18:00"
                })

        except Exception as e:
            ignored.append({
                "index_flux": idx,
                "origine": str(flux.get(col_dep, "")),
                "destination": str(flux.get(col_arr, "")),
                "motif": f"Erreur : {e}"
            })

    st.session_state["jobs_ignored"] = ignored

    return pd.DataFrame(jobs)

import os
import streamlit as st
import pandas as pd
import plotly.express as px

from modules.phase_0 import (
    initialiser_graphe_routier,
    calculer_matrice_hors_ligne,
    generer_jobs_atomises
)

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------

st.set_page_config(page_title="Logistique CHU Nantes", layout="wide")

if not os.path.exists("./data"):
    os.makedirs("./data")

if "step" not in st.session_state:
    st.session_state["step"] = 1


# -----------------------------------------------------------------------------
# OUTILS
# -----------------------------------------------------------------------------

def normalize_text(x):
    return str(x).strip().lower()


def find_sheet_name(sheet_names, keywords):
    """
    Retourne le premier onglet contenant au moins un des mots-clés.
    keywords : list[str]
    """
    for s in sheet_names:
        s_norm = normalize_text(s)
        if any(k in s_norm for k in keywords):
            return s
    return None


def find_column(columns, keywords, default=None):
    """
    Retourne la première colonne dont le nom contient un des mots-clés.
    keywords : list[str]
    """
    cols = list(columns)
    for c in cols:
        c_norm = normalize_text(c)
        if any(k in c_norm for k in keywords):
            return c
    return default


@st.cache_resource
def get_cached_graph(ville="Nantes, France"):
    return initialiser_graphe_routier(ville)


@st.cache_data
def load_all_data(file):
    try:
        xl = pd.ExcelFile(file)
        data = {sheet: xl.parse(sheet) for sheet in xl.sheet_names}
        return data, None
    except Exception as e:
        return None, str(e)


def extraire_flux_hebdo(df):
    """
    Met en forme les flux hebdomadaires en format long.
    Attend une table avec :
    - colonnes d'identification (sites, support, direction...)
    - colonnes Lundi ... Dimanche
    """
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    jours_cibles = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

    cols_jours = []
    for j in jours_cibles:
        match = next((c for c in df.columns if j.lower() in str(c).lower()), None)
        if match:
            cols_jours.append(match)

    if not cols_jours:
        return pd.DataFrame()

    cols_id = [c for c in df.columns if c not in cols_jours]

    df_long = df.melt(
        id_vars=cols_id,
        value_vars=cols_jours,
        var_name="Jour_Brut",
        value_name="Volume"
    )

    def clean_day(x):
        x = str(x).lower()
        for j in jours_cibles:
            if j.lower() in x:
                return j
        return str(x)

    df_long["Jour"] = df_long["Jour_Brut"].apply(clean_day)
    df_long["Volume"] = pd.to_numeric(df_long["Volume"], errors="coerce").fillna(0)

    return df_long[df_long["Volume"] > 0].copy()


# -----------------------------------------------------------------------------
# INTERFACE
# -----------------------------------------------------------------------------

st.title("🚚 Pilotage des Flux Logistiques")

uploaded_file = st.sidebar.file_uploader(
    "Charger le fichier Excel de paramétrage",
    type=["xlsx"]
)

if not uploaded_file:
    st.info("Charge un fichier Excel pour démarrer.")
    st.stop()

all_data, error = load_all_data(uploaded_file)

if error:
    st.error(f"Erreur de lecture du fichier Excel : {error}")
    st.stop()

# -----------------------------------------------------------------------------
# ETAPE 1 - CHARGEMENT ET ANALYSE DES FLUX
# -----------------------------------------------------------------------------

sheet_names = list(all_data.keys())

onglet_flux = find_sheet_name(sheet_names, ["flux"])
if onglet_flux is None:
    st.error("Aucun onglet de type 'flux' n'a été trouvé dans le fichier.")
    st.stop()

df_propre = extraire_flux_hebdo(all_data[onglet_flux])

if df_propre.empty:
    st.error("Aucun flux exploitable n'a été trouvé dans l'onglet des flux.")
    st.stop()

ordre_jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

col_support = find_column(
    df_propre.columns,
    ["support", "fonction support", "service"],
    default=df_propre.columns[0]
)

col_direction = find_column(
    df_propre.columns,
    ["aller / retour", "aller/retour", "direction", "sens"],
    default=None
)

st.header("🌍 Vue Globale : Charge Totale Cumulée")

if col_direction and col_direction in df_propre.columns:
    df_global = (
        df_propre
        .groupby(["Jour", col_direction, col_support], dropna=False)["Volume"]
        .sum()
        .reset_index()
    )

    fig_global = px.bar(
        df_global,
        x="Jour",
        y="Volume",
        color=col_support,
        facet_col=col_direction,
        title="Besoin total en transport par service",
        category_orders={"Jour": ordre_jours},
        barmode="stack",
        text_auto=".0f"
    )
else:
    df_global = (
        df_propre
        .groupby(["Jour", col_support], dropna=False)["Volume"]
        .sum()
        .reset_index()
    )

    fig_global = px.bar(
        df_global,
        x="Jour",
        y="Volume",
        color=col_support,
        title="Besoin total en transport par service",
        category_orders={"Jour": ordre_jours},
        barmode="stack",
        text_auto=".0f"
    )

fig_global.update_layout(yaxis_title="Total contenants / volume", height=500)
st.plotly_chart(fig_global, use_container_width=True)

st.divider()
st.header("📊 Détail par Fonction Support")

services = sorted(df_propre[col_support].dropna().astype(str).unique())

for svc in services:
    with st.expander(f"Analyse des volumes : {svc}", expanded=False):
        df_svc = df_propre[df_propre[col_support].astype(str) == str(svc)].copy()

        if col_direction and col_direction in df_svc.columns:
            df_cumul = (
                df_svc
                .groupby(["Jour", col_direction], dropna=False)["Volume"]
                .sum()
                .reset_index()
            )

            fig = px.bar(
                df_cumul,
                x="Jour",
                y="Volume",
                color=col_direction,
                title=f"Total quotidien - {svc}",
                category_orders={"Jour": ordre_jours},
                barmode="group",
                text_auto=".0f"
            )
        else:
            df_cumul = (
                df_svc
                .groupby(["Jour"], dropna=False)["Volume"]
                .sum()
                .reset_index()
            )

            fig = px.bar(
                df_cumul,
                x="Jour",
                y="Volume",
                title=f"Total quotidien - {svc}",
                category_orders={"Jour": ordre_jours},
                text_auto=".0f"
            )

        st.plotly_chart(fig, use_container_width=True)

if st.button("Valider et passer à la configuration de la flotte"):
    st.session_state["step"] = 2
    st.rerun()


# -----------------------------------------------------------------------------
# ETAPE 2 - PARAMETRAGE DE LA FLOTTE
# -----------------------------------------------------------------------------

if st.session_state.get("step") >= 2:
    st.success("Volumes validés. Paramétrez votre flotte.")

    st.divider()
    st.header("⚙️ Paramétrage de la Simulation")

    st.subheader("1. Flotte de véhicules et capacité de charge")

    onglet_v = find_sheet_name(sheet_names, ["véhicule", "vehicule", "vehicules", "flotte"])
    if onglet_v is None:
        st.error("Aucun onglet véhicule/flotte n'a été trouvé.")
        st.stop()

    df_v = all_data[onglet_v].copy()
    df_v.columns = [str(c).replace("\n", " ").strip() for c in df_v.columns]

    col_type_vehicule = find_column(df_v.columns, ["types", "type", "véhicule", "vehicule"], default=df_v.columns[0])
    col_poids = find_column(df_v.columns, ["poids max", "charge max", "ptac"], default=None)
    col_carbone = find_column(df_v.columns, ["carbone", "co2", "cout carbone"], default=None)

    if col_poids is None:
        st.error("Impossible de trouver une colonne de capacité/poids dans l'onglet véhicules.")
        st.stop()

    selected_vehicles = []

    cols_h = st.columns([0.5, 2, 1.5, 1.5, 2])
    cols_h[0].write("**Actif**")
    cols_h[1].write("**Type de véhicule**")
    cols_h[2].write("**Charge Max (kg)**")
    cols_h[3].write("**Marge sécu (%)**")
    cols_h[4].write("**Impact Carbone**")

    for i, row in df_v.iterrows():
        c1, c2, c3, c4, c5 = st.columns([0.5, 2, 1.5, 1.5, 2])

        v_name = str(row.get(col_type_vehicule, f"Véhicule {i+1}")).strip()
        is_active = c1.checkbox("", value=True, key=f"v_active_{i}")

        raw_poids = str(row.get(col_poids, "")).lower().replace(",", ".").strip()

        try:
            poids_val = raw_poids.replace("kg", "").replace(" ", "")
            is_tonnes = "t" in poids_val
            poids_val = poids_val.replace("t", "")
            poids_val = float(poids_val)
            if is_tonnes:
                poids_val *= 1000
        except Exception:
            poids_val = 0

        c2.write(f"**{v_name}**")
        c3.write(f"{int(poids_val)} kg")

        taux = c4.number_input(
            "Remplissage",
            min_value=50,
            max_value=100,
            value=100,
            step=5,
            key=f"v_taux_{i}",
            label_visibility="collapsed"
        )

        carbone_val = row.get(col_carbone, 0) if col_carbone else 0
        c5.write(f"🍃 {carbone_val} kg/km")

        if is_active and poids_val > 0:
            selected_vehicles.append({
                "id": v_name,
                "poids_max": poids_val * (taux / 100),
                "vitesse": 30,
                "data_origine": row.to_dict()
            })

    st.divider()

    if st.button("🚀 LANCER LE CALCUL DES TOURNÉES", type="primary", use_container_width=True):
        if not selected_vehicles:
            st.error("Veuillez sélectionner au moins un véhicule valide.")
            st.stop()

        st.session_state["selected_fleet"] = selected_vehicles

        with st.status("Initialisation du moteur de calcul...") as status:
            status.update(label="Chargement de la carte routière (OSM)...")
            G = get_cached_graph("Nantes, France")

            if G is None:
                st.error("Le graphe routier n'a pas pu être initialisé.")
                st.stop()

            onglet_sites = find_sheet_name(sheet_names, ["param sites", "sites", "site"])
            if onglet_sites is None:
                st.error("Aucun onglet de paramétrage des sites n'a été trouvé.")
                st.stop()

            df_sites_input = all_data[onglet_sites]

            status.update(label="Géocodage et calcul des distances réelles...")
            mat_dist, mat_temps, mapping_site_index = calculer_matrice_hors_ligne(G, df_sites_input)

            if mat_dist is None or mat_temps is None or mapping_site_index is None:
                st.error("Le calcul de la matrice n'a pas abouti.")
                st.stop()

            if st.session_state.get("geocoding_errors"):
                st.warning(
                    f"⚠️ {len(st.session_state['geocoding_errors'])} adresses n'ont pas été trouvées."
                )

            status.update(label="Génération du catalogue de tâches (Jobs)...")

            capa_max = selected_vehicles[0]["poids_max"]

            df_jobs = generer_jobs_atomises(
                df_flux=df_propre,
                mapping_site_index=mapping_site_index,
                matrice_dist=mat_dist,
                matrice_temps=mat_temps,
                capa_max=capa_max
            )

            st.session_state["matrice_temps"] = mat_temps
            st.session_state["df_jobs"] = df_jobs
            st.session_state["step"] = 3

            status.update(label="Phase 0 terminée !", state="complete")
            st.rerun()


# -----------------------------------------------------------------------------
# ETAPE 3 - RESULTATS
# -----------------------------------------------------------------------------

if st.session_state.get("step") >= 3:
    st.divider()
    st.header("📦 Étape 3 : Catalogue des Tâches (Jobs)")

    df_jobs = st.session_state.get("df_jobs")

    if df_jobs is not None and not df_jobs.empty:
        st.success(f"✅ {len(df_jobs)} jobs générés avec succès.")

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Jobs", len(df_jobs))
        c2.metric("Distance Totale (km)", f"{df_jobs['dist_km'].sum():.1f}")
        c3.metric("Temps de trajet cumulé", f"{int(df_jobs['temps_min'].sum())} min")

        with st.expander("Consulter le détail des jobs générés", expanded=True):
            st.dataframe(df_jobs, use_container_width=True)

        if "jobs_ignored" in st.session_state and st.session_state["jobs_ignored"]:
            with st.expander("⚠️ Flux ignorés / non appariés", expanded=False):
                st.dataframe(pd.DataFrame(st.session_state["jobs_ignored"]), use_container_width=True)

        st.subheader("🤖 Optimisation des Tournées")
        if st.button("🔍 Calculer le planning optimal (Branch & Price)", type="primary"):
            with st.spinner("Le solveur recherche la meilleure combinaison de tournées..."):
                st.info("Appel du moteur d'optimisation (Phase 1) en préparation...")
    else:
        st.error("Aucun job n'a pu être généré. Vérifie la correspondance des noms de sites entre flux et paramétrage des sites.")

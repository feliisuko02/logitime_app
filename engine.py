"""
Logitime — Motor de analisis vectorizado v3
Novedades: umbrales, margen_mant y score_weights parametrizables desde settings
"""

import pandas as pd
import numpy as np
from datetime import datetime

DATE_FORMATS = ["%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]


def _detectar_formato(series, sample=20):
    muestra = series.dropna().head(sample).astype(str).str.strip()
    for fmt in DATE_FORMATS:
        try:
            pd.to_datetime(muestra, format=fmt)
            return fmt
        except (ValueError, TypeError):
            continue
    return None

def parsear_columna_fecha(series):
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    fmt = _detectar_formato(series)
    if fmt:
        return pd.to_datetime(series, format=fmt, errors='coerce')
    return pd.to_datetime(series, errors='coerce', dayfirst=True)

def parsear_fecha_single(v):
    if pd.isna(v) or v is None: return pd.NaT
    if isinstance(v, (datetime, pd.Timestamp)): return pd.Timestamp(v)
    s = str(v).strip()
    for fmt in DATE_FORMATS:
        try: return pd.to_datetime(s, format=fmt)
        except: continue
    try: return pd.to_datetime(s)
    except: return pd.NaT


def _mapear(df):
    m = {}
    for col in df.columns:
        cl = col.strip().lower()
        if cl == "operario": m[col] = "operario"
        elif "denominaci" in cl and "operario" in cl: m[col] = "operario_nombre"
        elif cl == "propietario": m[col] = "cliente"
        elif "tipo de movimiento" in cl: m[col] = "tipo_operacion"
        elif "fecha de fin" in cl: m[col] = "fecha_fin"
        elif "fecha de inicio" in cl: m[col] = "fecha_inicio"
        elif "denominaci" in cl and "art" in cl: m[col] = "articulo"
        elif cl == "cantidad": m[col] = "cantidad"
    return df.rename(columns=m)


def leer_excel(file_bytes):
    df = pd.read_excel(file_bytes, engine="openpyxl")
    col_lower = {c: c.strip().lower() for c in df.columns}
    for col, cl in col_lower.items():
        if cl in ("operario", "propietario"):
            df[col] = df[col].astype(str)
        elif cl == "cantidad":
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("Int64")
    return df


def analizar_excel(df_mov, df_mant=None, archivo="", historico_ops=None,
                   umbrales=None, margen_mant=30, score_weights=None):
    """
    Motor principal. Parametros opcionales desde settings:
      umbrales: dict con {tipo: {normal, anomalia}}
      margen_mant: minutos de margen para cruzar con mantenimiento
      score_weights: dict {rate_w, excess_w, trend_w}
    """
    # Defaults
    if umbrales is None:
        umbrales = {
            "picking":   {"normal": 8,  "anomalia": 20},
            "packing":   {"normal": 5,  "anomalia": 12.5},
            "carga":     {"normal": 15, "anomalia": 37.5},
            "recepcion": {"normal": 12, "anomalia": 30},
            "default":   {"normal": 10, "anomalia": 25},
        }
    if score_weights is None:
        score_weights = {"rate_w": 50, "excess_w": 30, "trend_w": 20}

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if df_mov.empty:
        return _vacio(fecha, archivo, "Archivo vacio")

    df = _mapear(df_mov.copy())

    for col in ["operario", "fecha_fin", "tipo_operacion"]:
        if col not in df.columns:
            return _vacio(fecha, archivo, f"Columna '{col}' no encontrada. Columnas: {list(df_mov.columns)}")

    df["ts_fin"] = parsear_columna_fecha(df["fecha_fin"])
    if "fecha_inicio" in df.columns:
        df["ts_inicio"] = parsear_columna_fecha(df["fecha_inicio"])
    else:
        df["ts_inicio"] = pd.NaT

    df = df.dropna(subset=["operario", "ts_fin"])
    df["operario"] = df["operario"].astype(str).str.strip()
    if df.empty:
        return _vacio(fecha, archivo, "No hay filas con fechas validas")

    if "operario_nombre" not in df.columns: df["operario_nombre"] = df["operario"]
    if "cliente" not in df.columns: df["cliente"] = "SIN CLIENTE"
    else: df["cliente"] = df["cliente"].fillna("SIN CLIENTE")
    if "cantidad" not in df.columns: df["cantidad"] = 0
    else: df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce").fillna(0).astype(int)
    if "articulo" not in df.columns: df["articulo"] = ""
    df["tipo_operacion"] = df["tipo_operacion"].astype(str).str.lower().str.strip()

    mask_dur = df["ts_inicio"].notna() & df["ts_fin"].notna()
    df["duracion"] = np.where(mask_dur, (df["ts_fin"]-df["ts_inicio"]).dt.total_seconds()/60, 0.0)

    if historico_ops is None: historico_ops = {}

    umbral_map = {t: v["anomalia"] for t, v in umbrales.items()}
    default_umbral = umbrales.get("default", {}).get("anomalia", 25)

    rate_w = score_weights.get("rate_w", 50)
    excess_w = score_weights.get("excess_w", 30)
    trend_w = score_weights.get("trend_w", 20)

    anomalias = []
    resumen_ops = []

    for operario, grp in df.groupby("operario", sort=False):
        g = grp.sort_values("ts_fin").reset_index(drop=True)
        nombre = str(g["operario_nombre"].iloc[0])
        n = len(g)
        if n < 2:
            total_u = int(g["cantidad"].sum())
            dur_total = g["duracion"].sum()
            horas = dur_total / 60
            resumen_ops.append({
                "codigo": operario, "nombre": nombre, "total_movimientos": n,
                "total_anomalias": 0, "tiempo_promedio_entre": 0,
                "duracion_promedio": round(g["duracion"].mean(), 2),
                "total_unidades": total_u,
                "productividad": round(total_u/horas, 2) if horas > 0 else 0,
                "score_riesgo": 0, "tendencia": "estable",
            })
            continue

        deltas = g["ts_fin"].diff().dt.total_seconds().to_numpy() / 60
        deltas = deltas[1:]
        tipos = g["tipo_operacion"].values[:-1]

        umbs = np.array([umbral_map.get(t, default_umbral) for t in tipos])

        if operario in historico_ops and historico_ops[operario].get("dias", 0) >= 5:
            h = historico_ops[operario]
            dynamic = h["p95"] * 1.5
            umbs = np.maximum(umbs, dynamic)
            umbral_tipo = "dinamico"
        else:
            umbral_tipo = "fijo"

        anom_mask = deltas > umbs
        anom_indices = np.where(anom_mask)[0]
        anom_count = int(anom_mask.sum())

        for idx in anom_indices:
            i = int(idx)
            anomalias.append({
                "operario": operario, "operario_nombre": nombre,
                "picking_n": i+1, "picking_n1": i+2,
                "tiempo_entre": round(float(deltas[i]), 2),
                "umbral": round(float(umbs[i]), 2),
                "umbral_tipo": umbral_tipo,
                "exceso": round(float(deltas[i]-umbs[i]), 2),
                "tipo_operacion": str(tipos[i]),
                "fecha_fin_n": str(g.iloc[i]["fecha_fin"]),
                "fecha_fin_n1": str(g.iloc[i+1]["fecha_fin"]),
                "articulo": str(g.iloc[i].get("articulo", "")),
                "cliente": str(g.iloc[i].get("cliente", "")),
                "justificada": False, "motivo": None,
            })

        total_u = int(g["cantidad"].sum())
        dur_total = float(g["duracion"].sum())
        horas = dur_total / 60
        deltas_mean = float(np.mean(deltas))

        rate = anom_count / max(n-1, 1)
        excesos = deltas[anom_mask] - umbs[anom_mask]
        exc_avg = float(np.mean(excesos)) if len(excesos) > 0 else 0.0

        tend_score = 0
        tend = "estable"
        if operario in historico_ops and historico_ops[operario].get("dias", 0) >= 3:
            avg_h = historico_ops[operario].get("avg_anomalias", 0)
            if avg_h > 0:
                if anom_count > avg_h * 1.3:
                    tend_score = trend_w
                    tend = "empeorando"
                elif anom_count < avg_h * 0.7:
                    tend_score = -trend_w / 2
                    tend = "mejorando"

        score = min(100, max(0, round(
            rate * rate_w + min(exc_avg/30, 1) * excess_w + tend_score, 1)))

        resumen_ops.append({
            "codigo": operario, "nombre": nombre, "total_movimientos": n,
            "total_anomalias": anom_count,
            "tiempo_promedio_entre": round(deltas_mean, 2),
            "duracion_promedio": round(float(g["duracion"].mean()), 2),
            "total_unidades": total_u,
            "productividad": round(total_u/horas, 2) if horas > 0 else 0,
            "score_riesgo": score, "tendencia": tend,
        })

    if df_mant is not None and not df_mant.empty and anomalias:
        _cruzar_mantenimiento(anomalias, df_mant, margen_mant)

    resumen_cls = []
    if "cliente" in df.columns:
        cl_agg = df.groupby("cliente", sort=False).agg(
            total_movimientos=("operario","size"), total_unidades=("cantidad","sum"),
            duracion_total=("duracion","sum"), duracion_promedio=("duracion","mean"),
        ).reset_index()
        cl_agg["total_unidades"] = cl_agg["total_unidades"].astype(int)
        cl_agg["horas"] = cl_agg["duracion_total"] / 60
        cl_agg["productividad"] = np.where(cl_agg["horas"]>0, cl_agg["total_unidades"]/cl_agg["horas"], 0)
        for _, row in cl_agg.iterrows():
            resumen_cls.append({
                "nombre": str(row["cliente"]),
                "total_movimientos": int(row["total_movimientos"]),
                "total_unidades": int(row["total_unidades"]),
                "duracion_total": round(float(row["duracion_total"]), 2),
                "duracion_promedio": round(float(row["duracion_promedio"]), 2),
                "productividad": round(float(row["productividad"]), 2),
            })

    no_just = [a for a in anomalias if not a["justificada"]]
    justif = [a for a in anomalias if a["justificada"]]

    patrones = {}
    if no_just:
        horas_anom = []
        for a in no_just:
            ts = parsear_fecha_single(a["fecha_fin_n"])
            if not pd.isna(ts): horas_anom.append(ts.hour)
        if horas_anom:
            arr = np.array(horas_anom)
            patrones = {k: v for k, v in {
                "antes_10h": int(np.sum(arr<10)), "10h_14h": int(np.sum((arr>=10)&(arr<14))),
                "14h_18h": int(np.sum((arr>=14)&(arr<18))), "despues_18h": int(np.sum(arr>=18)),
            }.items() if v > 0}

    total_anom_visible = len(no_just)
    alertas = []
    for op in resumen_ops:
        if op["score_riesgo"] > 60:
            alertas.append({"tipo":"riesgo_alto",
                "msg":f"{op['nombre']} ({op['codigo']}) - Score: {op['score_riesgo']}/100", "nivel":"critical"})
    if total_anom_visible > 15:
        alertas.append({"tipo":"anomalias_altas", "msg":f"{total_anom_visible} anomalias detectadas", "nivel":"warning"})

    return {
        "fecha": fecha, "archivo": archivo,
        "total_movimientos": len(df), "total_anomalias": total_anom_visible,
        "anomalias_justificadas": len(justif),
        "tasa_anomalias_pct": round(total_anom_visible/max(len(df),1)*100, 2),
        "operarios": sorted(resumen_ops, key=lambda x: x["score_riesgo"], reverse=True),
        "clientes": sorted(resumen_cls, key=lambda x: x["total_unidades"], reverse=True),
        "anomalias_detalle": sorted(no_just, key=lambda x: x["exceso"], reverse=True),
        "patrones_hora": patrones, "alertas": alertas,
    }


def _cruzar_mantenimiento(anomalias, df_mant, margen_min=30):
    mant = df_mant.copy()
    mant_ts = mant_op = mant_desc = None
    for col in mant.columns:
        cl = col.strip().lower()
        if "fecha" in cl: mant[col] = mant[col].apply(parsear_fecha_single); mant_ts = col
        if cl == "operario": mant_op = col
        if "descripcion" in cl or "descripci" in cl: mant_desc = col
    if not mant_ts or not mant_op: return
    mv = mant.dropna(subset=[mant_ts, mant_op])
    if mv.empty: return
    mant_by_op = {}
    for _, m in mv.iterrows():
        op = str(m[mant_op]).strip()
        mant_by_op.setdefault(op, []).append(
            (m[mant_ts], m.get(mant_desc, "Registrado") if mant_desc else "Registrado"))
    for a in anomalias:
        if a["operario"] not in mant_by_op: continue
        ts_a = parsear_fecha_single(a["fecha_fin_n"])
        if pd.isna(ts_a): continue
        for mts, mdesc in mant_by_op[a["operario"]]:
            if abs((mts-ts_a).total_seconds())/60 <= margen_min:
                a["justificada"] = True
                a["motivo"] = f"Mantenimiento: {mdesc}"
                break


def _vacio(fecha, archivo, error=""):
    return {"fecha": fecha, "archivo": archivo, "error": error,
            "total_movimientos": 0, "total_anomalias": 0, "anomalias_justificadas": 0,
            "tasa_anomalias_pct": 0, "operarios": [], "clientes": [],
            "anomalias_detalle": [], "patrones_hora": {}, "alertas": []}

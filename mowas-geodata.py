#!/bin/env python3

import argparse
from osgeo import gdal
from osgeo import ogr
from osgeo import osr
import sys

osr.UseExceptions()



parser = argparse.ArgumentParser(
    description = "Verwaltungsgebiete für MoWaS-Alarmierung aufbereiten"
)

parser.add_argument(
    '-i', '--input',
    type = str,
    required = True,
    help = "Eingabedatei (VG5000-Datensatz)")

parser.add_argument(
    '-o', '--output',
    type = str,
    default = 'mowas.gpkg',
    help = "Ausgabedatei (MoWaS-Gebiete)")


ARGS = parser.parse_args()



WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)
WGS84.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)



def load_vg(path, layers):
    sys.stderr.write("Lade '%s'.\n" % path)

    ds = gdal.OpenEx(path, gdal.OF_READONLY)
    if ds is None:
        sys.stderr.write("Kann '%s' nicht öffnen.\n" % path)
        return

    arsdict = {}
    for lname in layers:
        l = ds.GetLayer(lname)

        if l is None:
            sys.stderr.write("Ebene '%s' in '%s' nicht vorhanden.\n" % ( lname, path ))
            continue

        if l.GetGeomType() not in [ ogr.wkbPolygon, ogr.wkbMultiPolygon ]:
            sys.stderr.write("Ebene '%s' in '%s' enthält keine Polygone.\n" % ( lname, path ))
            continue

        sys.stderr.write("  Lade Layer '%s'.\n" % lname)

        arsdict.update(load_vg_layer(l))

    return arsdict



def load_vg_layer(l):
    arsdict = {}
    count = 0

    for f in l:
        ars = f.ARS_0

        # Ungültige Regionalschlüssel überspringen
        if len(ars) != 12:
            continue

        # Nur Landflächen mit Struktur berücksichtigen
        if f.GF not in [ 4, 9 ]:
            continue

        if ars not in arsdict:
            arsdict[ars] = []

        geom = f.GetGeometryRef()
        geomtype = geom.GetGeometryType()
        if geomtype == ogr.wkbPolygon:
            arsdict[ars].append(geom.Clone())
        elif geomtype == ogr.wkbMultiPolygon:
            for i in range(geom.GetGeometryCount()):
                arsdict[ars].append(geom.GetGeometryRef(i).Clone())
        else:
            sys.stderr.write("    Nicht unterstützter Geometrietyp '%s' für Gebiet '%s'.\n" % ( geom.GetGeometryName(), ars ))
            continue

        count += 1

    sys.stderr.write("    %d Regionen mit %d Features geladen.\n" % ( len(arsdict), count ))

    return arsdict



def export_ars(arsdict):
    sys.stderr.write("Konvertiere %d Regionen. Das dauert etwas.\n" % len(arsdict))

    driver = ogr.GetDriverByName('GPKG')
    ds = driver.CreateDataSource(ARGS.output)
    layer = ds.CreateLayer('region', WGS84, ogr.wkbMultiPolygon)

    f_ars = ogr.FieldDefn('ARS', ogr.OFTString)
    f_ars.SetWidth(12)
    layer.CreateField(f_ars)

    for ars, geoms in arsdict.items():
        # Aus den Einzelteilen ein Multipolygon zusammensetzen
        multipolygon = ogr.Geometry(ogr.wkbMultiPolygon)
        multipolygon.AssignSpatialReference(geoms[0].GetSpatialReference())
        for geom in geoms:
            multipolygon.AddGeometry(geom)

        # Geometrie in das WGS84-Referenzsystem transformieren
        multipolygon.TransformTo(WGS84)

        # Feature ausgeben
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(multipolygon)
        feature.SetField('ARS', ars)
        layer.CreateFeature(feature)
        feature = None

    ds = None



ARS = {}
ARS.update(load_vg(ARGS.input, [ 'vg5000_sta', 'vg5000_lan', 'vg5000_rbz', 'vg5000_krs', 'vg5000_vwg', 'vg5000_gem' ]))
export_ars(ARS)

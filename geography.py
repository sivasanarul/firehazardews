"""Shared geographic configuration for SLIM fire-processing workflows."""

# Working resolution in meters
WORKING_RESOLUTION = 100  # meters

# Coordinate Reference Systems
PROCESSING_CRS = "EPSG:32735"  # UTM Zone 35S - equal area for Zambia
DELIVERY_CRS = "EPSG:4326"  # WGS84 for web delivery

# Zambia extent (approximate, in EPSG:4326)
ZAMBIA_EXTENT = {
    "xmin": 24.1643844080555539,
    "ymin": -18.1769576708333354,
    "xmax": 34.0196337080555509,
    "ymax": -9.0448398708333340,
}

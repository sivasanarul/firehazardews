# Burned Area & Land Cover Combined Map Feature

## Overview

This feature enhancement allows the Fire explorer map to intelligently combine Burned Area and Land Cover layers into a single comparison map when either or both are selected, while other layers get their own individual maps.

## Behavior

### Single Layer Selection
- **Burned Area only**: Shows in a single comparison map with its own title "Burned area & Land cover"
- **Land Cover only**: Shows in a single comparison map with its own title "Burned area & Land cover"
- **Both Burned Area and Land Cover**: Shows both layers in a single combined comparison map

### Multiple Layer Selection
When burned area and/or land cover are selected along with other layers:
- **Burned Area + Fire**: Two maps - one for Fire, one for Burned area & Land cover
- **Land Cover + FWI**: Two maps - one for FWI, one for Burned area & Land cover  
- **Burned Area + Land Cover + Fire + FWI**: Four maps - Fire, FWI, Vegetation fuel, Burned area & Land cover
- **Other layers** (Fire, FWI, Vegetation fuel, etc.): Continue to get their own individual maps

## Key Features

### Shared Comparison Map
- Both burned area and land cover layers render on the same leaflet map
- Users can overlay them and adjust individual opacity for each layer
- Reduces screen clutter and allows for direct comparison of burned areas with land cover patterns

### Independent Opacity Controls
- The combined map includes separate opacity sliders for:
  - Burned area opacity
  - Land cover opacity
- Each slider updates the corresponding original input element in the sidebar
- Changes are reflected in real-time on both the combined map and the main map

### Smart Grid Layout
The comparison grid automatically adjusts:
- 2x2 grid for up to 4 maps
- Automatically excluded layers that are incompatible with comparison mode (e.g., precipitation, hazard, recovery)
- Up to 4 panels maximum

### Status Display
For the combined burned area & land cover map:
- Shows loading/error status from both layers
- Displays combined status message (e.g., "Burned area loaded; Land cover loading")
- Properly reflects when either layer has an error or is pending

### Legend Rendering
- The legend for the combined map displays both layers' information:
  - Burned area scar visualization
  - Land cover class legends
- Both legends are rendered together for the combined panel

## Technical Implementation

### JavaScript Changes (map.js)

#### 1. **renderComparisonMaps() function** (lines 524-544)
- Added logic to group "burn" and "land" layers into a single "burn-land" key
- When both are selected: filters them out and inserts "burn-land" as a single entry
- When only one is selected: maps it to "burn-land" for unified handling
- Maintains proper ordering with other layers (fire, fwi, fuel)

#### 2. **Comparison Titles** (line 449)
- Added entry: `"burn-land": "Burned area & Land cover"`

#### 3. **datasetMap() function** (lines 458-463)
- Updated to route both "burn" and "land" layer requests to the shared "burn-land" comparison map
- Preserves backward compatibility by checking if the combined map exists first

#### 4. **Opacity Controls** (lines 603-664)
- For "burn-land" combined key, creates two independent opacity sliders:
  - Burned area opacity
  - Land cover opacity
- Each slider updates the corresponding original control in the sidebar

#### 5. **Layer Addition** (lines 697-706)
- For "burn-land" combined maps, adds both "burn" and "land" layers to the same comparison map
- Only adds layers that are actually selected (checked in layerStates)

#### 6. **refreshComparisonStatus() function** (lines 726-762)
- Enhanced to combine status messages from both layers for the "burn-land" map
- Shows errors/loading status from either layer
- Proper state handling: error state if either layer has error, loading if either is pending

#### 7. **renderLegend() function** (lines 2102-2114)
- For "burn-land" combined maps, includes both "burn" and "land" keys in the legend rendering
- Displays both burned area and land cover legend entries together

## User Experience Flow

1. **User selects layers**:
   - Checks "Burned area" and/or "Land cover" checkboxes in the Layers step

2. **Map layout automatically adjusts**:
   - If only one is selected or both are selected, they appear as a single map tile labeled "Burned area & Land cover"
   - Other selected layers appear in separate map tiles

3. **User interacts with the combined map**:
   - Can adjust opacity of each layer independently using separate sliders in the Legend panel
   - Can pan/zoom just this map or synchronize with other maps
   - Sees combined status and legend information

4. **Layer control remains in sidebar**:
   - Original "Burned area" and "Land cover" checkboxes in the sidebar remain independent
   - Opacity controls in the sidebar continue to work
   - Changes to opacity sync between sidebar and comparison map sliders

## Compatibility

- **Comparison mode limits**: Still respects the 4-panel maximum
- **Incompatible layers**: Still prevents comparison mode when hazard/recovery/precipitation layers are selected
- **Single map mode**: Users can select "Single map" to view all layers on one map (all layers share the main map)
- **Backward compatible**: Fire API endpoints remain unchanged

## Testing Scenarios

✓ Both burned area and land cover selected → Single "Burned area & Land cover" map
✓ Only burned area selected → Single "Burned area & Land cover" map  
✓ Only land cover selected → Single "Burned area & Land cover" map
✓ Burned area + Fire → Two maps (Fire and Burned area & Land cover)
✓ Land cover + FWI → Two maps (FWI and Burned area & Land cover)
✓ Burned area + Land cover + Fire → Two maps (Fire and Burned area & Land cover)
✓ All 5 layers + incompatible layer → Falls back to single map mode
✓ Opacity controls in comparison panel sync with sidebar controls
✓ Pan/zoom sync works across all maps including the combined one
✓ Legend displays correctly for combined map

## Performance Notes

- No additional API calls required (existing endpoints used)
- Tile caching still works per-layer (burn and land tiles cached separately)
- Comparison map synchronization includes the combined burn-land map
- Memory usage slightly reduced by sharing one map container for two layers

## Future Enhancements

- Could extend this pattern to other layer combinations (e.g., fire events + satellite imagery)
- Could add layer visibility toggles within the combined comparison panel legend
- Could allow custom layer grouping via user preferences

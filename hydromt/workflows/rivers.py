import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from scipy import ndimage
from typing import Union
import logging
from pyflwdir import Flwdir, FlwdirRaster

from ..gis_utils import spread2d

logger = logging.Logger(__name__)

__all__ = ["river_width", "river_depth"]


def river_width(
    gdf_stream: gpd.GeoDataFrame,
    da_rivmask: xr.DataArray,
    nmin=5,
) -> np.ndarray:
    """Return segment average river width based on a river mask raster.
    For each segment in gdf_stream the associated area is calculated from stream mask
    and divided by the segment length to obtain the average width.

    Parameters
    ----------
    gdf_stream : gpd.GeoDataFrame
        River segments
    da_rivmask : xr.DataArray
        Boolean river mask in projected grid.
    nmin : int, optional
        Minimum number of cells in rivmask to calculate the width, by default 5

    Returns
    -------
    rivwth : np.ndarray
        Average width per segment in gdf_stream
    """
    assert da_rivmask.raster.crs.is_projected
    gdf_stream = gdf_stream.copy()
    # get/check river length
    if "rivlen" not in gdf_stream.columns:
        gdf_stream["rivlen"] = gdf_stream.to_crs(da_rivmask.raster.crs).length
    # rasterize streams
    gdf_stream["segid"] = np.arange(1, gdf_stream.index.size + 1, dtype=np.int32)
    segid = da_rivmask.raster.rasterize(gdf_stream, "segid").astype(np.int32)
    segid.raster.set_nodata(0)
    segid.name = "segid"
    # remove islands to get total width of braided rivers
    da_mask = da_rivmask.copy()
    da_mask.data = ndimage.binary_fill_holes(da_mask.values)
    # find nearest stream segment for all river cells
    segid_spread = spread2d(segid, da_mask)
    # get average width based on da_rivmask area and segment length
    cellarea = abs(np.multiply(*da_rivmask.raster.res))
    seg_count = ndimage.sum(
        da_rivmask, segid_spread["segid"].values, gdf_stream["segid"].values
    )
    rivwth = seg_count * cellarea / gdf_stream["rivlen"]
    valid = np.logical_and(gdf_stream["rivlen"] > 0, seg_count > nmin)
    return np.where(valid, rivwth, -9999)


def river_depth(
    data: Union[xr.Dataset, pd.DataFrame, gpd.GeoDataFrame],
    method: str,
    flwdir: Union[Flwdir, FlwdirRaster] = None,
    min_rivdph: float = 1.0,
    manning: float = 0.03,
    qbankfull_name: str = "qbankfull",
    rivwth_name: str = "rivwth",
    rivzs_name: str = "rivzs",
    rivdst_name: str = "rivdst",
    rivslp_name: str = "rivslp",
    **kwargs,
) -> Union[xr.DataArray, np.ndarray]:
    """Derive river depth estimates based bankfull discharge.

    For a full overview of methods see Neal et al. (2021)
    
    Neal et al (2021) "Estimating river channel bathymetry in large scale flood inundation models", 
    Water Resour. Res., 57, https://doi.org/10.1029/2020wr028301

    Parameters
    ----------
    data : xr.Dataset, pd.DataFrame, gpd.GeoDataFrame
        Dataset/DataFrame containing required variables
    method : {'powlaw', 'manning', 'gvf'}
        Method to estimate the river depth:

        * powlaw:  power-law hc*Qbf**hp, requires qbankfull (Qbf) variable and
          optional hc (default = 0.27) and hp (default = 0.30)
        * manning: river depth for kinematic conditions, requires qbankfull, rivwth,
          rivslp and manning variables, optional min_rivslp (default = 1e-5)
        * gvf: gradually varying flow, requires qbankfull, rivwth, zs, rivdst and
          manning variables, optional min_rivslp (default = 1e-5)
    flwdir : Flwdir, FlwdirRaster, optional
        Flow directions, required if method is not powlaw
    min_rivdph : float, optional
        Minimum river depth [m], by default 1.0
    manning : float, optional
        Constant manning roughness [s/m^{1/3}], by default 0.03
    qbankfull_name, rivwth_name, rivzs_name, rivdst_name, rivslp_name: str, optional
        Name for variables in data: bankfull discharge [m3/s], river width [m],
        bankfull water surface elevation profile [m+REF], distance to river outlet [m],
        and river slope [m/m]

    Returns
    -------
    rivdph: xr.DataArray, np.ndarray
        River depth [m]. A DataArra is returned if the input data is a Dataset, otherwise
        a array with the shape of one input data variable is returned.
    """
    methods = ["powlaw", "manning", "gvf"]
    if method == "powlaw":

        def rivdph_powlaw(qbankfull, hc=0.27, hp=0.30, min_rivdph=1.0):
            return np.maximum(hc * qbankfull ** hp, min_rivdph)

        rivdph = rivdph_powlaw(data[qbankfull_name], min_rivdph=min_rivdph, **kwargs)
    elif method in ["manning", "gvf"]:
        assert flwdir is not None
        rivdph = flwdir.river_depth(
            qbankfull=data[qbankfull_name].values,
            rivwth=data[rivwth_name].values,
            zs=data[rivzs_name].values if rivzs_name in data else None,
            rivdst=data[rivdst_name].values if rivdst_name in data else None,
            rivslp=data[rivslp_name].values if rivslp_name in data else None,
            manning=manning,
            method=method,
            min_rivdph=min_rivdph,
            **kwargs,
        )
    else:
        raise ValueError(f"Method unknown {method}, select from {methods}")
    if isinstance(data, xr.Dataset):
        rivdph = xr.DataArray(
            dims=data.raster.dims, coords=data.raster.coords, data=rivdph
        )
        rivdph.raster.set_nodata(-9999.0)
        rivdph.raster.set_crs(data.raster.crs)
    return rivdph

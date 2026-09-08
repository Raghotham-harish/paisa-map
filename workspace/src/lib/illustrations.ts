// unDraw illustrations, recoloured to the brand palette (accent → #216A0B
// rupee green, secondary → #DFAE3A amber, structural ink → warm green-grey).
// See DESIGN.md § Empty-state illustrations. Pass one as
// `illustration={illustrations.noData}` to <EmptyState>.
import noData from "../assets/illustrations/no-data.svg";
import dataTrends from "../assets/illustrations/data-trends.svg";
import folderFiles from "../assets/illustrations/folder-files.svg";
import locationSearch from "../assets/illustrations/location-search.svg";

export const illustrations = {
  noData,        // generic "nothing here yet"
  dataTrends,    // forecast / analytics
  folderFiles,   // upload / store data
  locationSearch, // saved locations / map
};

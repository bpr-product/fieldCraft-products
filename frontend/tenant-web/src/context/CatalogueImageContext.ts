import React from 'react';

/**
 * Provides a map of catalogue image filename → fresh SAS URL to any component
 * in the Create Bid tree that renders CodeListItem images.
 * Populated in Requirements_v2 and consumed by SingleSelect_v2 / MultiChoice_v2.
 */
export const CatalogueImageContext = React.createContext<Record<string, string>>({});

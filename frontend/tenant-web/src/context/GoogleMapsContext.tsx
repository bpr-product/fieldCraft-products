/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 
* @author      : Kanimozhi.S
* @since       : 0.0.1
* Requirement# :
* Purpose      : Google Map Context
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE        |   AUTHOR     |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 18391           |  22-04-2026   |   Kanimozhi  | Google Map conrext created 
*
*
*/

import React from "react";
import { useJsApiLoader } from "@react-google-maps/api";

const libraries: any = ["places"];

interface GoogleMapsContextValue {
  isLoaded: boolean;
  mapApiKey: string;
}

export const GoogleMapsContext = React.createContext<GoogleMapsContextValue>({
  isLoaded: false,
  mapApiKey: "",
});

// Inner loader — only rendered when mapApiKey is non-empty, ensuring
// useJsApiLoader is called exactly once with the real key.
const GoogleMapsLoader: React.FC<{ mapApiKey: string; children: React.ReactNode }> = ({
  mapApiKey,
  children,
}) => {
  const { isLoaded } = useJsApiLoader({
    id: "google-map-script",
    googleMapsApiKey: mapApiKey,
    libraries,
  });

  return (
    <GoogleMapsContext.Provider value={{ isLoaded, mapApiKey }}>
      {children}
    </GoogleMapsContext.Provider>
  );
};

// Outer provider — gates rendering of GoogleMapsLoader until key is ready.
export const GoogleMapsProvider: React.FC<{ mapApiKey: string; children: React.ReactNode }> = ({
  mapApiKey,
  children,
}) => {
  if (!mapApiKey) {
    return (
      <GoogleMapsContext.Provider value={{ isLoaded: false, mapApiKey: "" }}>
        {children}
      </GoogleMapsContext.Provider>
    );
  }

  return <GoogleMapsLoader mapApiKey={mapApiKey}>{children}</GoogleMapsLoader>;
};

export const useGoogleMaps = () => React.useContext(GoogleMapsContext);

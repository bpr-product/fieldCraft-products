import { createContext, useState, useMemo, useContext  } from "react";
import { createTheme } from "@mui/material/styles";
import { TableHead, useMediaQuery } from "@mui/material";
import Header from "../components/Header";



// color design tokens export
export const tokens = (mode:any) => ({
  ...(mode === "dark"
    ? {
        grey: {
          100: "#e0e0e0",
          200: "#c2c2c2",
          300: "#a3a3a3",
          400: "#858585",
          500: "#666666",
          600: "#525252",
          700: "#3d3d3d",
          800: "#292929",
          900: "#141414"
        },
        primary: {
          100: "#d0d1d5",
          200: "#a1a4ab",
          300: "#727681",
          400: "#1F2A40",
          500: "#141b2d",
          600: "#101624",
          700: "#0c101b",
          800: "#080b12",
          900: "#040509"
        },
        greenAccent: {
          100: "#dbf5ee",
          200: "#b7ebde",
          300: "#94e2cd",
          400: "#70d8bd",
          500: "#4cceac",
          600: "#3da58a",
          700: "#2e7c67",
          800: "#1e5245",
          900: "#0f2922"
        },
        redAccent: {
          100: "#f8dcdb",
          200: "#f1b9b7",
          300: "#e99592",
          400: "#e2726e",
          500: "#db4f4a",
          600: "#af3f3b",
          700: "#832f2c",
          800: "#58201e",
          900: "#2c100f"
        },
        blueAccent: {
          100: "#e1e2fe",
          200: "#c3c6fd",
          300: "#a4a9fc",
          400: "#868dfb",
          500: "#6870fa",
          600: "#535ac8",
          700: "#3e4396",
          800: "#2a2d64",
          900: "#151632"
        },
        white:{
          100: "#121212", //overall bg color
          200: "#1e1e1e", //card bg color
          300: "#ffffffde",  //text color     
          400: "#1e1e1e", //sidebar bg color 
          500: "#ffffff0d",
          //600: "#ffffff73",//subtitle
          600: "#8c8c8c",//subtitle
        },
        gradient:{         
          1:'linear-gradient(250.38deg, rgb(230, 244, 255) 2.39%, rgb(105, 177, 255) 34.42%, rgb(22, 119, 255) 60.95%, rgb(9, 88, 217) 84.83%, rgb(0, 44, 140) 104.37%)'         
        }
      }
    : {
        grey: {
          100: "#141414",
          200: "#292929",
          300: "#3d3d3d",
          400: "#525252",
          500: "#666666",
          600: "#858585",
          700: "#a3a3a3",
          800: "#c2c2c2",
          900: "#e0e0e0"
        },
        primary: {         
          50:"#e8f0f7",
          60:"#d1e1ef",
          70:"#bad2e7",
          80:"#a3c3df",
          90:"#8cb4d7",          
          100: "#75a5cf",
          200: "#5e96c7",
          300: "#4787bf",
          400: "#3078b7",
          500: "#112D4E",         
          600: "#0f2946",
          700: "#0d243e",
          800: "#0b1f36",
          900: "#091a2e",          
        },
        greenAccent: {
          10:"#329c55",
          20:"#43ab65",
          100: "#0f2922",
          200: "#1e5245",
          300: "#2e7c67",
          400: "#3da58a",
          500: "#4cceac",
          600: "#70d8bd",
          700: "#94e2cd",
          800: "#b7ebde",
          900: "#dbf5ee",
          1000:'#107907',
          1100:'#168316'
        },
        redAccent: {
          100: "#2c100f",
          200: "#58201e",
          300: "#832f2c",
          400: "#af3f3b",
          500: "#db4f4a",
          600: "#e2726e",
          700: "#e99592",
          800: "#f1b9b7",
          900: "#f8dcdb",
          1000:'#a80101'
        },
        blueAccent: {
          100: "#151632",
          200: "#2a2d64",
          300: "#3e4396",
          400: "#535ac8",
          500: "#6870fa",
          600: "#868dfb",
          700: "#a4a9fc",
          800: "#c3c6fd",
          900: "#e1e2fe",
          1000:' #035283'
        },
        white:{
          100: "#f8f7fa", //overall bg color
          200: "#ffffff", //card bg color
          300: "#262626", //text color      
          400: "#e6e6e6", //sidebar bg color  
          500: "#e6ebf1", //card border-color
          600: "#8c8c8c",//subtitle
          700: "#fafafb"
        },
        gradient:{         
          1:'linear-gradient(250.38deg, rgb(230, 244, 255) 2.39%, rgb(105, 177, 255) 34.42%, rgb(22, 119, 255) 60.95%, rgb(9, 88, 217) 84.83%, rgb(0, 44, 140) 104.37%)',
          2:"linear-gradient(to right,#3c5bfa, #7f00ffd4)",
        }
       
      })     
});

// mui theme settings
export const themeSettings = (mode:any) => {
  const colors = tokens(mode);
  return {
    palette: {
      mode: mode,
      ...(mode === "dark"
        ? {
            // palette values for dark mode
            primary: {
              main: colors.primary[500],                      
            },
            secondary: {
              main: colors.greenAccent[500]
            },
            neutral: {
              dark: colors.grey[700],
              main: colors.grey[500],
              light: colors.grey[100]
            },
            background: {
              default: colors.primary[500]
            },         
          }
        : {
            // palette values for light mode
            primary: {
              main: colors.primary[500],             
            },
            secondary: {
              main: colors.greenAccent[500]
            },
            neutral: {
              dark: colors.grey[700],
              main: colors.grey[500],
              light: colors.grey[100]
            },
            background: {
              default: colors.white[100]
            },
          })
    },
    typography: {
      fontFamily: ["Poppins", "sans-serif"].join(","),     
      fontSize: 13,     
      textTransform: 'none',
      h1: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 40
      },
      h2: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 32
      },
      h3: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 24
      },
      h4: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 20
      },
      h5: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 16
      },
      h6: {
        fontFamily: ["Poppins", "sans-serif"].join(","),
        fontSize: 14
      }
    },
    TableHead:{
      fontFamily: ["Poppins", "sans-serif"].join(","),
      fontSize: 26,
    }  

  };
};



export type ColorMode = {
  theme: any 
  setCopyy:(theme: any) => void
}

// context for color mode
export const ColorModeContext = createContext<ColorMode>({
  theme: createTheme(themeSettings("light")),
  setCopyy: () => {}, 
});


export const useGlobalContext = () => useContext(ColorModeContext)

export const ToggleMode = () =>{
  //const theme = useMemo(() => createTheme(themeSettings("light")), ["light"]);
  var x = "20";
  createTheme(themeSettings("light"));
}

export const useMode = () => {
  const [mode, setMode] = useState("dark");
  const colorMode = useMemo(
    () => ({
      toggleColorMode: () =>
        setMode((prevMode) => (prevMode === "dark" ? "light" : "dark"))
    }),
    []
  );

  const theme = useMemo(() => createTheme(themeSettings(mode)), [mode]);

  return [theme, colorMode];
};



export interface CollapseMode {
  isSidebarCollapse: boolean;
  contentMargin?: string;
}

export type CollapseModeContext = {
  sidebarCollapseState: CollapseMode,  
  setCollapse:(sidebarCollapseState: CollapseMode) => void
}

// context for Sidebar Collapse mode
export const SidebarCollapseModeContext = createContext<CollapseModeContext>({
  sidebarCollapseState: getCollapseMode(),
  setCollapse: () => {}, 
});

// context for Sidebar Collapse mode
//export const SidebarCollapseModeContext = createContext<CollapseModeContext | null>(null);


export function getCollapseMode (isSmallScreen?:boolean) {
  var defaultCollapseMode : CollapseMode= {
    isSidebarCollapse: false, 
    contentMargin :"main-custom" 
  };  
  if(isSmallScreen){
    defaultCollapseMode.contentMargin = undefined;
  }
  return defaultCollapseMode; 
  //setClassName(toCollapse ? "main-margin-custom" : "main-custom");
}

export const useGlobalSidebarContext = () => useContext(SidebarCollapseModeContext)













// export const useMode = () => {
//   const [mode, setMode] = useState<'light' | 'dark'>('light');

//   const colorMode = useMemo(
//     () => ({
//       toggleColorMode: () => {
//         setMode((prevMode) =>
//           prevMode === 'light' ? 'dark' : 'light'
//         );
//       },
//     }),
//     []
//   );

//   const theme = useMemo(() => createTheme(themeSettings(mode)), [mode]);
//   return [colorMode, theme] as const; // this should solve your problem.
// };



// export type GlobalContent = {
//   copy: string
//   setCopy:() => void
// }

// export const MyGlobalContext = createContext<GlobalContent>({
// copy: 'Hello World', // set a default value
// setCopy: () => {},
// })

// export const useGlobalContext = () => useContext(MyGlobalContext)

// export const getCopy = (userType: string) => 
// {
//    if (userType.toLowerCase() === 'admin')
//    {
//       return 'Hello Admin User!'
//    }
//    return 'Welcome user!'
// }

// export const toggleColorMode = (mode: string) => 
// {
//    if (mode.toLowerCase() === 'dark')
//    {
//       return 'dark'
//    }
//    return 'Welcome user!'
// }


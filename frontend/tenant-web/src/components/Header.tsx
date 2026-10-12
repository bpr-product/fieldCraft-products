import { Box, Typography, useTheme } from "@mui/material";
import { tokens } from "../hooks/theme";

type HeaderProps = {
  title: string;
  subtitle: string|null;
};



const Header = ({ title, subtitle }: HeaderProps) => {
  const theme = useTheme();
  const colors = tokens(theme.palette.mode);
  return (
    <Box className="header">
      <Typography 
        variant="h3"
        color="rgb(60 60 60 / 87%)"
        fontWeight="Medium"
        sx={{ mb: "1px" }}
      >
        {title}
      </Typography>
      <Typography variant="h5" color={colors.white[300]}>
        {subtitle}
      </Typography>
    </Box>
  );
};

export default Header;

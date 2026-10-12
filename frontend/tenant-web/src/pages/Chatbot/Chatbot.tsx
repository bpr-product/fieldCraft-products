import React, { useState } from "react";
import { useTranslation } from "react-i18next";
import Chats from './Chats';
import { NextSteps } from "./NextSteps";
import "./Chatbot.scss";
import {
  Button, IconButton, useTheme, Typography, TextField,
  Divider, Fab, Tooltip
} from "@mui/material";
import SendIcon from '@mui/icons-material/Send';
import { tokens } from "../../hooks/theme";
import image from "../../assets/images/google-chat-logo-512x512.png";
import PhonelinkSetupIcon from '@mui/icons-material/PhonelinkSetup';
import DragIndicatorIcon from '@mui/icons-material/DragIndicator';
import InfoIcon from '@mui/icons-material/Info';
import { postChatBot } from "../../services/ChatBotService";
import { useOidcAccessToken } from "@axa-fr/react-oidc";


interface ResponseBotObject {
  purpose: string;
  message: string;
  options?: string[];
  sender: string;
}

const Chatbot: React.FC = () => {
  const { t } = useTranslation();
  const [isChatbotClose, setIsChatbotClose] = useState(true);

  const toggleChatbot = () => {
    setIsChatbotClose(!isChatbotClose);
  };
  const theme = useTheme();
  const colors = tokens(theme.palette.mode);
  const [userResponse, setUserResponse] = useState<string>("");
  const [step, setStep] = useState<number>(0);
  const [botResponse, setBotResponse] = useState<any>({
    purpose: "",
    message: "",
    sender: "FieldCraft",
    // options:["Why did my bid cost changed"]
  });
  const [sendUserResponse, setSendUserResponse] = useState<string>("");
  const { accessToken, accessTokenPayload } = useOidcAccessToken();
  const responseMessage = React.useRef<any>();



  const setNextStep = async (response: string) => {
    let data = {
      "question": userResponse,
      "context": "bid"
    }
    let message = await postChatBot(data, accessToken);
    setStep(prevState => prevState + 1);
    setSendUserResponse(response);
    // let res = NextSteps(step, response, responseMessage.current);
    setBotResponse({ ...botResponse, message: message.data, sender: "FieldCraft" });
    setUserResponse("");
  };

  const optionClick = (e: React.MouseEvent<HTMLElement>) => {
    let option = e.currentTarget.dataset.id;
    if (option) {
      setNextStep(option);
    }
  };

  // const handleInputChange = (e) => {
  //   setUserResponse(e.target.value);
  // };


  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setNextStep(userResponse);
  };


  return (
    <div >
      {/* {isChatbotClose &&  */}
      <div className="chat-container" style={{ position: 'fixed', bottom: 15, right: 140 }}>
        <div className="chat-header" style={{ display: 'flex', alignItems: 'center', }} >
          <img
            src={image}
            alt={t("chatbot.logoAlt")}
            style={{ width: '70px', height: '70px' }}
          />
          <Typography variant="h2" gutterBottom color="white" >
            {t("chatbot.title")}
          </Typography>
          <Tooltip title={t("chatbot.guidelineInfo")} placement="bottom">
            <IconButton style={{ color: "white", marginLeft: "55%", width: '40px', height: '40px' }}>
              < InfoIcon />
            </IconButton>
          </Tooltip>
        </div>
{/* 
        <Chats
          userResponse={userResponse}
          botResponse={botResponse}
          sendUserResponse={sendUserResponse}
          optionClick={optionClick}
        /> */}

        <form onSubmit={e => handleSubmit(e)} className="form-container">
          <TextField id="chatbot-textfield-type-a-message"
            fullWidth
            className="custom-textfield"
            value={userResponse}
            onChange={(e) => setUserResponse(e.target.value)}
            placeholder={t("chatbot.typeMessage")}
            variant="outlined"
          />
          <Tooltip title={t("chatbot.startChat")} placement="bottom" arrow>
            <Button
              className="custom-button"
              variant="outlined"
              color="primary"
              type="submit"
             id="chatbot-button-button">
              <SendIcon />
            </Button>
          </Tooltip>
        </form>

      </div>
      {/* } */}
    </div>
  )
}

export default Chatbot;



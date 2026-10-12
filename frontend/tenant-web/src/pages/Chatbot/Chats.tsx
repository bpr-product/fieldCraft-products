import React, { useState, useEffect, useRef } from "react";
import "./Chats.scss";
import TouchAppIcon from '@mui/icons-material/TouchApp';
import SendIcon from '@mui/icons-material/Send';
import PersonPinIcon from '@mui/icons-material/PersonPin';
import { display } from "@mui/system";
import MarkUnreadChatAltIcon from '@mui/icons-material/MarkUnreadChatAlt';
import { useTranslation } from "react-i18next";

interface Props {
  userResponse: string;
  botResponse: {
    purpose: string;
    message: string;
    options?: string[];
    sender: string;
  };
  sendUserResponse: string;
  optionClick: (ev: React.MouseEvent<HTMLElement>) => void;
}

interface MessagesInfo {
  purpose?: string;
  message: string;
  options?: string[];
  sender: string;
  timestamp: string;
}

const Chats: React.FC<Props> = (props) => {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<MessagesInfo[]>([]);
  const dummyRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (messages.length === 0) {
      setMessages([
        {
          purpose: "introduction",
          message: t("chatbot.greeting"),
          // options:["Why did my Bid scheduled Changed"],
          sender: "FieldCraft",
          timestamp: getTimeStamp(),
        },
      ]);
    } else {
      let tempArray = [...messages];
      tempArray.push({
        message: props.sendUserResponse,
        sender: "You",
        timestamp: getTimeStamp(),
      });
      setMessages(tempArray);

      setTimeout(() => {
        let temp2 = [...tempArray];
        temp2.push({ ...props.botResponse, timestamp: getTimeStamp() });
        setMessages(temp2);
      }, 1000);
    }
  }, [props.sendUserResponse, props.botResponse]);

  useEffect(() => {
    if (
      dummyRef &&
      dummyRef.current &&
      bodyRef &&
      bodyRef.current
    ) {
      bodyRef.current.scrollTo({
        top: dummyRef.current.offsetTop,
        behavior: "smooth",
      });
    }
  }, [messages]);

  const getTimeStamp = (): string => {
    const now = new Date();
    const hours = now.getHours();
    const minutes = now.getMinutes();
    const period = hours >= 12 ? 'PM' : 'AM';
    const formattedHours = hours % 12 === 0 ? 12 : hours % 12;
    const formattedMinutes = minutes < 10 ? `0${minutes}` : minutes;

    return `${formattedHours}:${formattedMinutes} ${period}`;
  };

  return (
    <div className="message-container" ref={bodyRef} style={{height:"550px"}}>
      {messages.map((chat) => (
        <div key={chat.message}>
          <span style={{ fontSize: "14px", color: chat.sender === 'FieldCraft' ? "grey" : "orange", marginRight: chat.sender === 'FieldCraft' ? "auto" : "26px", marginLeft: chat.sender === 'FieldCraft' ? "12px" : "auto", }}>
            {chat.sender}
          </span>
          <div className={`message ${chat.sender}`} style={{ display: 'flex', alignItems: 'center' }}>
            <p>{chat.message}</p>
          </div>

          <span className="timestamp" style={{ fontSize: "11px", color: "grey", marginRight: chat.sender === 'FieldCraft' ? "auto" : "12px", marginLeft: chat.sender === 'FieldCraft' ? "3px" : "auto" }}>
            {t("common.today")} {chat.timestamp}
          </span>
          {chat.options ? (
            <div className="options">
              <div>
                <p> <TouchAppIcon /></p>
              </div>
              {chat.options.map((option) => (
                <p
                  onClick={(e) => props.optionClick(e)}
                  data-id={option}
                  key={option}
                >
                  {option}
                </p>
              ))}
            </div>
          ) : null}
          <div ref={dummyRef} className="dummy-div"></div>
        </div>
      ))}
    </div>
  );
};

export default Chats;

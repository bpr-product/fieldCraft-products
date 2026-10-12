/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : 20-Nov-2023
* @author      : shiyam kumar
* @since       : 0.0.1
* Requirement# :
* Purpose      : Listing of Bid added to Cart
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE         |   AUTHOR          |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
* 1103           |  18-04-2024    |   shiyamkumar     | Bid Chat Cost Issue Fix
* 
*/


import React, { useEffect, useState } from 'react';
import ChatIcon from '@mui/icons-material/Chat';
import './FloatChatbot.scss';
import {  Fab, SxProps, Zoom, useTheme } from '@mui/material';
import ChatCard from './Chat';
import { getProjectInfoByProjectId, postChatBot } from '../../services/ChatBotService';
import { useOidcAccessToken } from '@axa-fr/react-oidc';
import { ChatOptions } from '../../viewmodel/Chat';
import { StringBuilder } from 'typescript-string-operations';
import { TenantContext } from '../../hooks/UserSession';




const fabStyle = {
  position: 'absolute',
  bottom: 16,
  right: 16,
};



const FloatChatbot = () => {
  const theme = useTheme();
  const [isChatbotOpen, setIsChatbotOpen] = useState(false);
  const { accessToken } = useOidcAccessToken();

  const [userResponse] = useState<string>("");
  const [options, setOptions] = useState<ChatOptions[]>([]);
  const [step, setStep] = useState<number>(0);
  const [botResponse, setBotResponse] = useState<any>({
    purpose: "",
    message: "",
    sender: "FieldCraft",
    // options:["Why did my bid cost changed"]
  });
  const [sendUserResponse, setSendUserResponse] = useState<string>("");
  const [selectedBid, setSelectedBid] = useState<string>("");
 const [tenantId, setTenantId] = React.useState<String>();
const { tenantSessionDetails } = React.useContext(TenantContext);

 const url = new URL(window.location.href);
 const queryParam = url.search.substring(1); 
  useEffect(() => {
    if(!queryParam.toString()){
      fetchData();
    }

  }, []);

  const fetchData = async () => {
    setTenantId(tenantSessionDetails?.tenantid);
  }


  const buildString = (data: any) => {
    if (data) {
      var builder = new StringBuilder();

      builder.append("Order No: " + data.jobNo + "\n\n");
      builder.append("Project Name: " + data.trade + "\n\n");
      builder.append("Cost: " + "$" + data.totalCost + "\n\n");
      builder.append("Status: " + data.status + "\n\n");

      var dataString = builder.toString();
      return dataString;
    }

  }

  const prepareQuestion = (selectedOption: any) => {
    var questionDTO = {
      "question": selectedOption.option.replace("?", "") + " for " + tenantId+"_"+selectedBid + " ?",
      "context": "project",
      "contextId": tenantId+"_"+selectedBid
    };
    return questionDTO;



  }


  const setNextStep = async (response: ChatOptions) => {
    
    if (response && response.option) {
      let questionDTO = {
        "question": userResponse,
        "context": "project",
        "contextId": "tenantId_orderNo"
      }
      let message: any;
      if (response.type == "BidId") {
        setSelectedBid(response.option);
        let responseData = await getProjectInfoByProjectId(response.option, accessToken);

        const projectStatus = responseData?.data?.status;

        if (!projectStatus) {
          console.error("Project status not found in responseData");
          return;
        }
        let dataString = buildString(responseData.data);
        message = dataString;


        const categorizedQuestions = {

          "Contract Approval": [
        
            { option: "Who can I contact for support regarding this construction ?", type: "Question" },
            { option: "What is the schedule of my project ?", type: "Question" },
            // { option: "How to Upload my contract?", type: "Question" },
            { option: "How much should I pay as advance amount for this project?", type: "Question" },
            { option: "Why has the cost of my Project Estimate changed? ", type: "Question" },
            { option: "What is the process for getting a contract approved?", type: "Question" },
            
          
          ],
          "Contract Upload":[
        
            // { option: "How to Upload my contract?", type: "Question" },
            { option: "How much should I pay as advance amount for this project?", type: "Question" },
            { option: "Who can I contact for support regarding this construction ?", type: "Question" },
            { option: "Why has the cost of my Project Estimate changed? ", type: "Question" },
          ],
          "Payment": [

            { option: "When is the construction slated to begin?", type: "Question" },  
            { option: "Who can I contact for support regarding this construction ?", type: "Question" },
            { option: "How long will the construction of this project take?", type: "Question" },
            { option: "Whether the schedule of my Project changed?", type: "Question" },


          ],
          "PO Generation": [
            { option: "When is the construction slated to begin?", type: "Question" }, 
            { option: "Who can I contact for support regarding this construction ?", type: "Question" },
            { option: "How long will the construction of this project take?", type: "Question" },
            { option: "Whether the schedule of my Project changed?", type: "Question" },

          ],
          "Project Active": [
            { option: "How long will the construction of this project take?", type: "Question" },
            { option: "Who can I contact for support regarding this construction ?", type: "Question" },
            { option: "When will this construction get over ?", type: "Question" },
            { option: "Are you anticipating any risks or issues that could affect the project timeline?", type: "Question" },
           
          ],
        };

        // Set options based on project status
        const chatOptions: ChatOptions[] = categorizedQuestions[projectStatus] || [];
        if (chatOptions.length === 0) {
          console.warn(`No predefined questions for project status: ${projectStatus}`);
        }
        setOptions(chatOptions);

      }
      else {
        let questionDTO = prepareQuestion(response);
        let responseData = await postChatBot(questionDTO, accessToken);
        message = responseData.data;
      }

      setStep(prevState => prevState + 1);

      setSendUserResponse(response.option);

      if (message !== undefined)
        setBotResponse({ ...botResponse, message: message.toString(), sender: "FIELDCRAFT" });

    }
  };

  const optionClick = (userResp: ChatOptions) => {

    if (userResp) {
      setNextStep(userResp);
    }
  };

  const toggleChatbot = () => {
    setIsChatbotOpen(!isChatbotOpen);
  };

  const [value, setValue] = React.useState(0);

  const transitionDuration = {
    enter: theme.transitions.duration.enteringScreen,
    exit: theme.transitions.duration.leavingScreen,
  };

  const fabs = [
    {
      color: 'primary' as 'primary',
      sx: fabStyle as SxProps,
      icon: <ChatIcon />,
      label: 'Add',
    }
  ];

  return (


    <>

      {fabs.map((fab, index) => (
        <Zoom
          key={fab.color}
          in={value === index}
          timeout={transitionDuration}
          style={{
            transitionDelay: `${value === index ? transitionDuration.exit : 0}ms`,
          }}
          unmountOnExit
        >
          {isChatbotOpen ? (
            <Fab sx={fab.sx} size="medium" aria-label={fab.label} color={fab.color} onClick={toggleChatbot} style={{ position: 'fixed' }}>
              {fab.icon}
            </Fab>
          ) : (
            <Fab sx={fab.sx} size="medium" aria-label={fab.label} color={fab.color} onClick={toggleChatbot} style={{ position: 'fixed' }}>
              {fab.icon}
            </Fab>
          )}
        </Zoom>
      ))}

      {isChatbotOpen &&
        <ChatCard
          userResponse={userResponse}
          botResponse={botResponse}
          sendUserResponse={sendUserResponse}
          options={options}
          optionClick={optionClick}
        ></ChatCard>
      }
    </>
  );
};

export default FloatChatbot;
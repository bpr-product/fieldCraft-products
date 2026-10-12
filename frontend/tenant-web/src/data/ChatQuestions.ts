import { MessagesInfo } from "../viewmodel/Chat";

export const defaultMessage = (userName:string):MessagesInfo => {
    if(userName){
        return {
            purpose: "",
            message:`Hi ${userName},\n
            I'm Eden, How can I help you`,
            // options: [],
            sender: "FIELDCRAFT",
            timestamp: ""
        }; 
    }else{
        return {
            purpose: "",
            message: `Hi, Which order no do you need help with today?`,
            //options: [],
            sender: "FIELDCRAFT",
            timestamp: ""
        }; 
    }
    
};

// export const NextSteps = (step, userResponse, apiResponse) => {
//     return step === 0
//       ? {
//           purpose: "specify field",
//           message: `Nice to meet you, ${userResponse}!  We have some job positions for you. Which of these call out to you?`,
//           // options: ["Frontend", "Backend", "Full Stack"]
//           options: ["Why my bid price Changed", "Backend", "Full Stack"]
//         }
//       : step === 1
//       ? userResponse === "Frontend"
//         ? {
//             purpose: "specify frontend role",
//             message: "Great choice! Now, which specific Frontend role are you interested in?",
//             options: ["UI/UX Designer", "React Developer", "Angular Developer"]
//           }
//         : 
//         userResponse === "Backend"
//         ? {
//             purpose: "specify frontend role",
//             message: "Great choice! Now, which specific Backend role are you interested in?",
//             options: ["Java", "Python", "C"]
//           }
//         :{
//             purpose: "specify experience",
//             message: `That's cool ${userResponse}!  Could you describe your experience in that field?`
//           }
//       : step === 2
//       ? {
//           purpose: "specify email",
//           message: "Could you describe your email?"
//         }
//       : step === 3
//       ? {
//           purpose: "end",
//           message:
//             "Thank you so much for spending time chatting with me. Good luck with the application process! I hope we can meet soon. Bye!"
//         }
//       : {
//           purpose: "bye",
//           message: "Bye!"
//         };
//   };
  


// sample json data
// export const rows = [
//     {
//         "id": 1,
//         "name": "Asish",
//         "companyName": "FIELDCRAFT",
//         "phoneNo": 9961159264,
//         "openBalance": 67
//     }
// ];

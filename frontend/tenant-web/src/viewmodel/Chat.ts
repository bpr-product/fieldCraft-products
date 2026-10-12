
export interface MessagesInfo {
    purpose?: string;
    message: string;
    // options?: string[];
    sender: string;
    timestamp: string;
}

export interface ChatOptions {
    option?:string;
    type?:string;
}

export interface BidInfo {
    id?: string;
    customerUid?: string;
    orderNo?: string;
    trade?: string;
    location?: string
    status?: string;
    startDate?: Date;
    endDate?: Date;
    totalCost?: string;
}


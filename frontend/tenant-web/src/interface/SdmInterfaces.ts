export interface SDM {
    Building: Building;
    AdminData: AdminData;
    BuildingData: BuildingData;
    xmlns: string;
    FileType: string;
    FileOID: string;
    CreationDateTime: string;
    ODMVersion: string;
    SourceSystem: string;
}

export interface Building {
    GlobalVariables: GlobalVariables;
    TemplateVersion: TemplateVersion[];
    MethodDef?: MethodDef[];
    BasicDefinition: BasicDefinitions;
    OID: string;
}

export interface GlobalVariables {
    Name: string;
    Description: string;
    ProtocolName: string;
}

export interface TemplateVersion {
    Protocol: Protocol;
    EventDef: EventDef[];
    FormDef: FormDef[];
    ItemGroupDef: ItemGroupDef[];
    ItemDef: ItemDef[];
    CodeList: CodeList[];
    ConditionDef: ConditionDef[];
    Mappings: Mappings[];
    Images?: ImageConfig[];
    OID: string;
    Name: string;
    Include?: { OID: string };
    MethodDef?: MethodDef[];
}

export interface ImageConfig {
    baseUrl: string;
}

export interface Protocol {
    EventRef: EventRef[];
}

export interface EventRef {
    EventOID: string;
    Mandatory: string;
}

export interface EventDef {
    Description: Description;
    FormRef: FormRef[];
    OID: string;
    Name: string;
    Repeating: string;
    Type: string;
}

export interface FormRef {
    FormOID: string;
    Mandatory: string;
}

export interface FormDef {
    Description: Description;
    ItemGroupRef: ItemGroupRef[];
    OID: string;
    Name: string,
    Repeating: string;
}

export interface ItemGroupRef {
    ItemGroupOID: string;
    Mandatory: string;
    CollectionExceptionConditionOID?: string;
    MethodOID?: string;
    SummarizeInstances?: string;
}

export interface ItemGroupDef {
    Description: Description;
    ItemRef: ItemRef[];
    OID: string;
    Name: string;
    Repeating: string;
    ItemDef: ItemDef[];
    ParentItemGroupRepeatKey?: number;
}

export interface ItemRef {
    ItemOID: string;
    Mandatory: string;
    CollectionExceptionConditionOID?: string;
    ShowOn?: string;
    MethodOID?: string;
}

export interface RangeCheck {
    Comparator: 'LT' | 'GT' | 'EQ' | 'NE' | 'LE' | 'GE';
    SoftHard: string;
    CheckValue: string;
    ErrorMessage: {
        TranslatedText: {
            lang: string;
            text: string;
        }
    };
}

export interface ItemDef {
    Question: Description;
    OID: string;
    Name: string;
    DataType: string;
    CodeListRef?: CodeListRef;
    MeasurementOid?: string;
    MetaKey?: string;
    Category?: string;
    ItemGroupRepeatKey?: number;
    ImageUrl?: string;
    ThumbnailUrl?: string;
    RangeCheck?: RangeCheck[];
}

export interface CodeListRef {
    CodeListOID: string;
}

export interface CodeList {
    CodeListItem: CodeListItem[];
    OID: string;
    Name: string;
    DataType: string;
    ItemGroupRepeatKey?: number;
}

export interface CodeListItem {
    Decode: Description;
    CodedValue: string;
    IsEnabled: boolean;
    ImageUrl?: string;
    ThumbnailUrl?: string;
}

export interface ConditionDef {
    Description: Description;
    FormalExpression: FormalExpression;
    OID: string;
    Name: string;
}



export interface Description {
    TranslatedText: TranslatedText;
}

export interface TranslatedText {
    lang: string;
    text: string;
}

export interface FormalExpression {
    Context: string;
    text: string;
}
export interface MeasurementUnit {
    Name: string;
    OID: string;
    Symbol: {
        TranslatedText: TranslatedText | TranslatedText[];
    };
}
export interface MethodDef {
    Description: Description;
    FormalExpression: FormalExpression;
    OID: string;
    Name: string;
    MethodType: string;
}

export interface BasicDefinitions {
    MeasurementUnit: MeasurementUnit[];
}

export interface AdminData {
    User: User;
    OID: string;
}

export interface User {
    FirstName: string;
    LastName: string;
    OID: string;
}

export interface BuildingData {
    //AuditRecord: AuditRecord;
    EventData: EventData[];
    Key: string;
    OID: string;
    MetaDataVersionOID: string;
}

export interface AuditRecord {
    UserRef: UserRef;
    LocationRef: LocationRef;
    DateTimeStamp: string;
}

export interface UserRef {
    UserOID: string;
}

export interface LocationRef {
    LocationOID: string;
}

export interface EventData {
    FormData: FormData[];
    EventOID: string;
}

export interface FormData {
    ItemGroupData: ItemGroupData[];
    FormOID: string;
    TransactionType: string;
}

export interface ItemGroupData {
    ItemData: ItemData[];
    ItemGroupOID: string;
    ItemGroupRepeatKey: number;
    ParentItemGroupRepeatKey?: number;
}

export interface ItemData {
    ItemOID: string;
    Value: string;
    CodeList: CodeList[];
    MetaKey: string;
    ItemGroupRepeatKey: number;
    ItemGroupOID?: string;  // Optional: The ItemGroup this item belongs to (used for multi-instance groups)
    ParentItemGroupRepeatKey?: number;  // Optional: For nested instances, the parent instance index
    isDeleted?: boolean;  // Optional: Indicates if this derived item should be deleted/hidden
    rangeCheckFailed?: boolean;
}


export interface Mappings {
    Name: string;
    Source: string;
    LastUpdated: string;
    Description: string;
    Versions: Versions[];
    MetaKey: string;
}

export interface Versions {
    VersionOID: string;
    VersionName: string;
    CreatedBy: string;
    CreatedDate: string;
    ItemGroupDef: ItemGroupDef[];

}



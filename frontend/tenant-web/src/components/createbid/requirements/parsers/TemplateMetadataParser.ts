/*
* Copyright (C) FIELDCRAFT Inc - All Rights Reserved
* This file is part of FieldCraft. - Proprietary and confidential
* Unauthorized copying of this file, via any medium is strictly prohibited
* 
* Created on   : Oct-2025
* @author      : mohamed.riyaz
* @since       : 2.0.0
* Requirement# : BID-2.0
* Purpose      : Template Metadata Parser - Extracts template identity and metadata from SDM JSON
* -----------------------------------------------------------------------------
* Revision History
* -----------------------------------------------------------------------------
* Requirement/
* Issue/WorkItem |   DATE      |    AUTHOR       |   DESCRIPTION OF CHANGE
* -----------------------------------------------------------------------------
*/

/**
 * Template Metadata Parser
 * Extracts basic template identity and metadata from SDM JSON
 */

import type { TemplateMetadata } from '../types/TemplateTypes';
import { resolveMergedTemplateVersion } from '../../../../utils/sdmMergeUtils';

export class TemplateMetadataParser {
  private sdmJson: any;

  constructor(sdmJson: any) {
    this.sdmJson = sdmJson;
  }

  /**
   * Parse template metadata from SDM JSON
   */
  parse(): TemplateMetadata {
    const templateVersion = resolveMergedTemplateVersion(
      this.sdmJson?.SDM?.Building?.TemplateVersion
    );
    const globalVars = this.sdmJson?.SDM?.Building?.GlobalVariables;

    if (!templateVersion || !globalVars) {
      throw new Error('Invalid SDM template structure');
    }

    const protocol = templateVersion.Protocol;
    const formDef = templateVersion.FormDef?.[0];
    const eventDef = templateVersion.EventDef?.[0];

    return {
      tradeName: globalVars.Name || '',
      tradeType: globalVars.ProtocolName || '',
      description: globalVars.Description || '',
      protocolName: globalVars.ProtocolName || '',
      formOID: formDef?.OID || 'F.1',
      eventOID: protocol?.EventRef?.[0]?.EventOID || eventDef?.OID || 'E.1'
    };
  }

  /**
   * Get template version data
   */
  getTemplateVersion(): any {
    return resolveMergedTemplateVersion(this.sdmJson?.SDM?.Building?.TemplateVersion);
  }

  /**
   * Check if template is valid
   */
  isValid(): boolean {
    try {
      const templateVersion = this.getTemplateVersion();
      return !!(
        templateVersion &&
        templateVersion.FormDef &&
        templateVersion.ItemGroupDef &&
        templateVersion.ItemDef
      );
    } catch {
      return false;
    }
  }
}

/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 25-Mar-2025
 * @author      : Abirami.R
 * @since       : 0.0.1
 * Requirement# :
 * Purpose      : ETL Data Transformation
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE     |   AUTHOR     |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *    4119        | 25-03-2025 | Abirami      | Data Transformation for extracted data
 */

package com.fieldcraft.tenant.utils;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Date;
import java.util.List;
import java.util.Map;

public class ETLDataTransformationUtil {

    private static final ObjectMapper objectMapper = new ObjectMapper();
    private static final Logger logger = LogManager.getLogger(ETLDataTransformationUtil.class);
    private static final DateTimeFormatter dateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss");



    public static List<Map<String, Object>> transformData(List<Map<String, Object>> data) {
        for (Map<String, Object> row : data) {
            try {
                for (Map.Entry<String, Object> entry : row.entrySet()) {
                    String key = entry.getKey();
                    Object value = entry.getValue();

                    if (value == null) {
                        continue;
                    }
                    if ((key.equals("Cost") || key.equals("TotalCost")) && value instanceof BigDecimal) {
                        row.put(key, ((BigDecimal) value).doubleValue());
                    }
                    else if ((key.equals("VendorId") || key.equals("MaterialId")) && value instanceof Long) {
                        row.put(key, value.toString());
                    }
                    else if (key.equals("AnswersData") && value instanceof String) {
                        try {
                            Object jsonValue = objectMapper.readValue((String) value, Object.class);
                            row.put(key, jsonValue);
                        } catch (Exception e) {
                            System.err.println("Failed to parse answers_data: " + value);
                            row.put(key, value);
                        }
                    }
                    else if (value instanceof OffsetDateTime) {
                        Date date = Date.from(((OffsetDateTime) value).toInstant());
                        row.put(key, date);
                    } else if (value instanceof LocalDateTime) {
                        Date date = Date.from(((LocalDateTime) value).atZone(ZoneId.systemDefault()).toInstant());
                        row.put(key, date);
                    }


                    else {
                        row.put(key, value);
                    }
                }
            } catch (Exception e) {
                logger.error("An unexpected error occurred in ETLDataTransformationUtil: " + e.getMessage(), e);
            }
        }
        return data;
    }
}


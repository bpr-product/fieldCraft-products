/*
 * Copyright (C) FIELDCRAFT Inc - All Rights Reserved
 * This file is part of FieldCraft. - Proprietary and confidential
 * Unauthorized copying of this file, via any medium is strictly prohibited
 *
 * Created on   : 27-June-2024
 * @author      : Raja.M
 * @since       : 0.3.3
 * Requirement# :
 * Purpose      : receive notification message using Azure WebPubSub Service
 * -----------------------------------------------------------------------------
 * Revision History
 * -----------------------------------------------------------------------------
 * Requirement/
 * Issue/WorkItem |   DATE    |   AUTHOR  |   DESCRIPTION OF CHANGE
 * -----------------------------------------------------------------------------
 *
 *
 *
 * Provider PubSub | 2026-08-20 | Shanmugapriyan G (Claude-assisted) | Added getClientAccessUrlWithGroups()
 *                                — capability-free auto-join groups alongside existing role-granted groups.
 */
package com.fieldcraft.azure.messaging;

import com.azure.messaging.webpubsub.WebPubSubServiceClientBuilder;
import com.azure.messaging.webpubsub.client.WebPubSubClient;
import com.azure.messaging.webpubsub.client.WebPubSubClientBuilder;
import com.azure.messaging.webpubsub.models.GetClientAccessTokenOptions;
import https://fieldcraft.example.invalid;
import https://fieldcraft.example.invalid;
import com.fieldcraft.utils.StringUtils;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class AzurePubSubSubscriber extends MessagingSubscriptionClient {

    private String connectionString;
    private String hub;

    private WebPubSubClient client;

    public AzurePubSubSubscriber(){

    }

    public AzurePubSubSubscriber(Map<String, String> props){
        initialize(props);
    }

    //initialize
    public void initialize(Map<String, String> props) {
        this.connectionString = props.get("endpoint");
        this.hub = props.get("hub");
    }

    @Override
    public void connectSubscriptionClient(List<String> groupNames) {

        //create service client
        var accessUrl = getClientAccessUrl(groupNames);
        System.out.println("accessUrl: " + accessUrl);
        if(accessUrl == null) return ;

        try {
            // create WebPubSub client
            client = new WebPubSubClientBuilder().clientAccessUrl(accessUrl)
                    .buildClient();
            //subscribe events
            client.addOnGroupMessageEventHandler(event -> {
                System.out.println("Received group message from "
                        + event.getGroup() + ": " + event.getData().toString());

                //notify to subscribers
                onMessageReceived(event.getGroup(), event.getData().toString());
            });

            client.addOnServerMessageEventHandler(event -> {
                System.out.println("Received server message: "
                        + event.getData().toString());

                //notify to subscribers
                onMessageReceived(event.getData().toString());
            });

            //start listener client
            client.start();

            //join group
            joinGroups(groupNames);
        }catch (Exception ex){
            System.out.println(ex);
        }
    }

    @Override
    public void DisconnectSubscriptionClient() {

    }

    //get client access url
    @Override
    public String getClientAccessUrl(List<String> groupNames) {
        return getClientAccessUrl(groupNames,30);
    }

    //get client access url
    @Override
    public String getClientAccessUrl(List<String> groupNames, int expireInSec) {

        //validate parameter
        if(StringUtils.isNullOrWhitespace(connectionString) || StringUtils.isNullOrWhitespace(hub)){
            return null;
        }

        //create service client
        var serverClient = new WebPubSubServiceClientBuilder()
                .connectionString(connectionString)
                .hub(hub)
                .buildClient();

        var tokenOptions = new GetClientAccessTokenOptions();
        tokenOptions.setExpiresAfter(Duration.ofSeconds(expireInSec));
        for (String groupName : groupNames)
        {
            tokenOptions.addRole("webpubsub.joinLeaveGroup." + groupName.toLowerCase());
            tokenOptions.addRole("webpubsub.sendToGroup." + groupName.toLowerCase());
        }

        return serverClient.getClientAccessToken(tokenOptions).getUrl();
    }

    @Override
    public String getClientAccessUrlWithGroups(List<String> roleGroups, List<String> autoJoinGroups,
                                               int expireInSec) {
        if (StringUtils.isNullOrWhitespace(connectionString) || StringUtils.isNullOrWhitespace(hub)) {
            return null;
        }

        var serverClient = new WebPubSubServiceClientBuilder()
                .connectionString(connectionString)
                .hub(hub)
                .buildClient();

        var tokenOptions = new GetClientAccessTokenOptions();
        tokenOptions.setExpiresAfter(Duration.ofSeconds(expireInSec));
        for (String groupName : roleGroups) {
            if (StringUtils.isNullOrWhitespace(groupName)) {
                continue;
            }
            tokenOptions.addRole("webpubsub.joinLeaveGroup." + groupName.toLowerCase());
            tokenOptions.addRole("webpubsub.sendToGroup." + groupName.toLowerCase());
        }
        for (String groupName : autoJoinGroups) {
            if (StringUtils.isNullOrWhitespace(groupName)) {
                continue;
            }
            tokenOptions.addGroup(groupName.toLowerCase());
        }

        return serverClient.getClientAccessToken(tokenOptions).getUrl();
    }

    //join groups
    private void joinGroups(List<String> groupNames){
        try {
            for (String groupName : groupNames) {
                if (!StringUtils.isNullOrWhitespace(groupName)) {
                    client.joinGroup(groupName.toLowerCase());
                    System.out.println("join.. " + groupName);
                }
            }
        }
        catch (Exception ex){
            System.out.println(ex);
        }
    }
}

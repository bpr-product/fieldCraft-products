/*
 * Click nbfs://nbhost/SystemFileSystem/Templates/Licenses/license-default.txt to change this license
 * Click nbfs://nbhost/SystemFileSystem/Templates/Classes/Class.java to edit this template
 */
package com.fieldcraft.tenant.data.domain.accounts;

import jakarta.persistence.*;

/**
 *
 * @author Pavithra
 */
@Entity
@Table(name = "chart_of_account")
public class ChartOfAccount {
    @Id
    @Column(nullable = false, updatable = false)
    @GeneratedValue(
            strategy = GenerationType.IDENTITY
    )
    private Long id;

    @Column(nullable = false, length = 16)
    private String code;

    @Column(nullable = false, length = 64)
    private String name;

    @Column(nullable = false, length = 64)
    private String accountNo;
    @Column(nullable = true, length = 64)
    private String accountType;

    @Column(nullable = true, length = 128)
    private String description;

    @Column(nullable = true, length = 16)
    private String codeProvider;

    @Column(nullable = true, length = 16)
    private String currencyCode;

    @Column(nullable = true, length = 64)
    private String detailType;

    @Column(nullable = false)
    private boolean builtin;

    @Column(nullable = false)
    private boolean active;

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getCode() {
        return code;
    }

    public void setCode(String code) {
        this.code = code;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getAccountType() {
        return accountType;
    }

    public void setAccountType(String accountType) {
        this.accountType = accountType;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public String getCodeProvide() {
        return codeProvider;
    }

    public void setCodeProvide(String codeProvide) {
        this.codeProvider = codeProvide;
    }

    public String getCurrencyCode() {
        return currencyCode;
    }

    public void setCurrencyCode(String currencyCode) {
        this.currencyCode = currencyCode;
    }

    public boolean isBuiltin() {
        return builtin;
    }

    public void setBuiltin(boolean builtin) {
        this.builtin = builtin;
    }

    public boolean isActive() {
        return active;
    }

    public void setActive(boolean active) {
        this.active = active;
    }

    public String getAccountNo() {
        return accountNo;
    }

    public void setAccountNo(String accountNo) {
        this.accountNo = accountNo;
    }

    public String getCodeProvider() {
        return codeProvider;
    }

    public void setCodeProvider(String codeProvider) {
        this.codeProvider = codeProvider;
    }

    public String getDetailType() {
        return detailType;
    }

    public void setDetailType(String detailType) {
        this.detailType = detailType;
    }
}

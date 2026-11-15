package com.slope.platform.controller;

import com.slope.platform.common.ApiResponse;
import com.slope.platform.dto.SiteRequest;
import com.slope.platform.entity.Site;
import com.slope.platform.service.SiteService;
import jakarta.validation.Valid;
import java.util.List;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 点位接口。
 *
 * <p>Controller 只做三件事：接参数、调 Service、包统一响应。
 * 业务判断一律下沉到 Service，这样将来加定时任务或其它入口（如 MQ 消费）时逻辑不会漏掉。
 */
@RestController
@RequestMapping("/api/sites")
public class SiteController {

    private final SiteService siteService;

    public SiteController(SiteService siteService) {
        this.siteService = siteService;
    }

    @GetMapping
    public ApiResponse<List<Site>> list(
            @RequestParam(name = "slope_group", required = false) String slopeGroup) {
        return ApiResponse.ok(siteService.list(slopeGroup));
    }

    @GetMapping("/{id}")
    public ApiResponse<Site> get(@PathVariable Integer id) {
        return ApiResponse.ok(siteService.get(id));
    }

    @PostMapping
    public ApiResponse<Site> create(@Valid @RequestBody SiteRequest request) {
        return ApiResponse.ok(siteService.create(request));
    }

    @PutMapping("/{id}")
    public ApiResponse<Site> update(@PathVariable Integer id, @Valid @RequestBody SiteRequest request) {
        return ApiResponse.ok(siteService.update(id, request));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable Integer id) {
        siteService.delete(id);
        return ApiResponse.ok();
    }
}

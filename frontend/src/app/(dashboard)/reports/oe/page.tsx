'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { BarChart3, ShoppingCart, Users, TrendingUp } from "lucide-react";
import Link from "next/link";

export default function OEReportsPage() {
  const reportCategories = [
    {
      title: "Sales Analysis",
      description: "Sales performance and trend analysis",
      icon: TrendingUp,
      reports: [
        { name: "Sales by Product", href: "/reports/oe/sales-product" },
        { name: "Sales by Customer", href: "/reports/oe/sales-customer" },
        { name: "Sales Trends", href: "/reports/oe/sales-trends" }
      ],
      color: "text-blue-600"
    },
    {
      title: "Order Reports",
      description: "Order processing and status reports",
      icon: ShoppingCart,
      reports: [
        { name: "Order Status Report", href: "/reports/oe/order-status" },
        { name: "Backorder Report", href: "/reports/oe/backorders" },
        { name: "Order History", href: "/reports/oe/order-history" }
      ],
      color: "text-green-600"
    },
    {
      title: "Customer Analysis",
      description: "Customer behavior and performance",
      icon: Users,
      reports: [
        { name: "Customer Rankings", href: "/reports/oe/customer-rankings" },
        { name: "Customer Activity", href: "/reports/oe/customer-activity" },
        { name: "Customer Profitability", href: "/reports/oe/customer-profit" }
      ],
      color: "text-purple-600"
    },
    {
      title: "Performance Reports",
      description: "Operational performance metrics",
      icon: BarChart3,
      reports: [
        { name: "Fulfillment Report", href: "/reports/oe/fulfillment" },
        { name: "Shipping Performance", href: "/reports/oe/shipping" },
        { name: "Commission Report", href: "/reports/oe/commissions" }
      ],
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Order Entry Reports</h1>
        <p className="text-muted-foreground">
          Analyze sales performance, order processing, and customer behavior
        </p>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {reportCategories.map((category) => {
          const IconComponent = category.icon;
          return (
            <Card key={category.title} className="hover:shadow-md transition-shadow">
              <CardHeader className="flex flex-row items-center space-y-0 pb-2">
                <div className={`p-2 rounded-lg bg-gray-100 ${category.color}`}>
                  <IconComponent className="h-6 w-6" />
                </div>
                <div className="ml-4">
                  <CardTitle className="text-base">{category.title}</CardTitle>
                  <CardDescription className="text-sm">
                    {category.description}
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-2">
                  {category.reports.map((report) => (
                    <Link key={report.href} href={report.href}>
                      <Button variant="ghost" className="w-full justify-start text-sm">
                        {report.name}
                      </Button>
                    </Link>
                  ))}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Quick Stats</CardTitle>
          <CardDescription>Current order entry metrics and KPIs</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">$89,250</div>
              <div className="text-sm text-muted-foreground">Monthly Sales</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">156</div>
              <div className="text-sm text-muted-foreground">Orders This Month</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">98.5%</div>
              <div className="text-sm text-muted-foreground">Fill Rate</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-orange-600">2.1</div>
              <div className="text-sm text-muted-foreground">Avg Ship Days</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

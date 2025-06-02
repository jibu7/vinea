'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FileText, TrendingDown, Building2, Calendar } from "lucide-react";
import Link from "next/link";

export default function APReportsPage() {
  const reportCategories = [
    {
      title: "Aging Reports",
      description: "Vendor aging and payment due analysis",
      icon: Calendar,
      reports: [
        { name: "Vendor Aging Summary", href: "/reports/ap/aging-summary" },
        { name: "Vendor Aging Detail", href: "/reports/ap/aging-detail" },
        { name: "Payment Due Report", href: "/reports/ap/payment-due" }
      ],
      color: "text-blue-600"
    },
    {
      title: "Purchase Analysis",
      description: "Purchase spending and trends",
      icon: TrendingDown,
      reports: [
        { name: "Purchases by Vendor", href: "/reports/ap/purchases-vendor" },
        { name: "Purchases by Period", href: "/reports/ap/purchases-period" },
        { name: "Purchase Trends", href: "/reports/ap/purchase-trends" }
      ],
      color: "text-green-600"
    },
    {
      title: "Vendor Reports",
      description: "Vendor statements and profiles",
      icon: Building2,
      reports: [
        { name: "Vendor List", href: "/reports/ap/vendor-list" },
        { name: "Vendor Performance", href: "/reports/ap/vendor-performance" },
        { name: "Payment History", href: "/reports/ap/payment-history" }
      ],
      color: "text-purple-600"
    },
    {
      title: "Transaction Reports",
      description: "Detailed transaction listings",
      icon: FileText,
      reports: [
        { name: "Purchase Order Register", href: "/reports/ap/po-register" },
        { name: "Invoice Register", href: "/reports/ap/invoice-register" },
        { name: "Payment Register", href: "/reports/ap/payment-register" }
      ],
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Payable Reports</h1>
        <p className="text-muted-foreground">
          Analyze vendor data, aging, purchase spending, and AP transactions
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
          <CardDescription>Current AP metrics and KPIs</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">$32,450</div>
              <div className="text-sm text-muted-foreground">Total AP</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">28.5</div>
              <div className="text-sm text-muted-foreground">Avg Payment Days</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">$8,750</div>
              <div className="text-sm text-muted-foreground">Due This Week</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-orange-600">89</div>
              <div className="text-sm text-muted-foreground">Active Vendors</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

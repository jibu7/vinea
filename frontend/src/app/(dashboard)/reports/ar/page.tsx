'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FileText, TrendingUp, Users, Calendar } from "lucide-react";
import Link from "next/link";

export default function ARReportsPage() {
  const reportCategories = [
    {
      title: "Aging Reports",
      description: "Customer aging and past due analysis",
      icon: Calendar,
      reports: [
        { name: "Customer Aging Summary", href: "/reports/ar/aging-summary" },
        { name: "Customer Aging Detail", href: "/reports/ar/aging-detail" },
        { name: "Past Due Analysis", href: "/reports/ar/past-due" }
      ],
      color: "text-blue-600"
    },
    {
      title: "Sales Analysis",
      description: "Sales performance and trends",
      icon: TrendingUp,
      reports: [
        { name: "Sales by Customer", href: "/reports/ar/sales-customer" },
        { name: "Sales by Period", href: "/reports/ar/sales-period" },
        { name: "Sales Trends", href: "/reports/ar/sales-trends" }
      ],
      color: "text-green-600"
    },
    {
      title: "Customer Reports",
      description: "Customer statements and profiles",
      icon: Users,
      reports: [
        { name: "Customer Statements", href: "/reports/ar/statements" },
        { name: "Customer List", href: "/reports/ar/customer-list" },
        { name: "Credit Limit Report", href: "/reports/ar/credit-limits" }
      ],
      color: "text-purple-600"
    },
    {
      title: "Transaction Reports",
      description: "Detailed transaction listings",
      icon: FileText,
      reports: [
        { name: "Invoice Register", href: "/reports/ar/invoice-register" },
        { name: "Payment Register", href: "/reports/ar/payment-register" },
        { name: "Credit Memo Register", href: "/reports/ar/credit-register" }
      ],
      color: "text-orange-600"
    }
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Accounts Receivable Reports</h1>
        <p className="text-muted-foreground">
          Analyze customer data, aging, sales performance, and AR transactions
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
          <CardDescription>Current AR metrics and KPIs</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-4">
            <div className="text-center">
              <div className="text-2xl font-bold text-blue-600">$25,750</div>
              <div className="text-sm text-muted-foreground">Total AR</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-green-600">32.5</div>
              <div className="text-sm text-muted-foreground">Avg Days Outstanding</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-purple-600">85%</div>
              <div className="text-sm text-muted-foreground">Collection Rate</div>
            </div>
            <div className="text-center">
              <div className="text-2xl font-bold text-orange-600">124</div>
              <div className="text-sm text-muted-foreground">Active Customers</div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
